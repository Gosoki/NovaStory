#!/usr/bin/env python
"""A6 分析管线 v3(基础层)—— 从 SQLite 计算客观评价栈里【确定性、无需 API】的指标。

覆盖 docs/paper/03 §4(客观评价栈)/§7(完整可计算清单) 的可立即计算部分:
  结构完整度 / 逐镜头保真 / 版本演化 / 努力再分配 / 主观复合 / 条件×题目多样性 / E 引导剂量。
待补(需真数据或 API,后续增量):
  embedding 相对基线保真 Δ(需 embedding + baseline_gen)、LMM/TOST 统计检验、图表。

用法:
  .venv/bin/python analysis/v3.py [--db data/novastory.db] [--out data/analysis/v3_per_trial.csv]

设计:纯读、不改库;对旧库/未完成轮容忍(缺列/缺问卷 → NaN)。取代 v2 的
metrics.py/stats.py(HLZ 时代);全部 A6 完成并在真数据上验收后再删旧文件。
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import prereg, textstats  # noqa: E402
from core import config  # noqa: E402
from core.shots import parse_shots, strip_format  # noqa: E402

DEFAULT_DB = ROOT / "data" / "novastory.db"
_SHOT_FIELDS = ("shot_type", "visual", "audio", "duration")
_TAGS = prereg.SHOT_TAGS   # 与 views/questionnaire 同源,别各写一份


# ---------------- load ----------------

def included_participants(db_path) -> set[int]:
    """分析纳入的被试 id —— **每一个取数口都必须用它**,不能各写各的。

    两条规则:
      ① 研究员注入的 dev 被试(`screening_json.dev`)不算;
      ② `prereg.ANALYSIS_REQUIRES_ALL_ROUNDS` 为真时,只留**走完全部 N_ROUNDS 轮**的人,
         以**问卷提交数**为准(不看 `participants.status` —— 三轮问卷都交了、只差最后
         那份总问卷没点的人,任务数据是完整的)。

    ⚠️ 抽出来不是为了整洁。同意书里写着「中止された場合、そこまでの回答は分析には
    使用しません」,而此前这条规则**只在 v3.load() 里生效**;`events` / `pilot_check` /
    `embed` / `judge` 各自直接读库,其中 **`embed` 与 `judge` 会把被试原文再送一次给
    OpenAI** —— 也就是说离脱者的文字照样被外发,那句承诺当场就是假的。
    (`pilot_check` 更是用了第三种口径 `status=='done'`,同一份报告里两个分母。)"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        parts = pd.read_sql("SELECT id, screening_json FROM participants", con)
        quest = pd.read_sql("SELECT participant_id, round_idx FROM questionnaires", con)
    finally:
        con.close()
    ids = {int(r.id) for r in parts.itertuples()
           if not _loads(r.screening_json, {}).get("dev")}
    if prereg.ANALYSIS_REQUIRES_ALL_ROUNDS:
        if len(quest):
            n = (quest.drop_duplicates(["participant_id", "round_idx"])
                      .groupby("participant_id").size())
            ids &= {int(i) for i in n[n >= config.N_ROUNDS].index}
        else:
            ids = set()
    return ids


def load(db_path: Path) -> pd.DataFrame:
    """trials ⟕ questionnaires,按 (participant_id, round_idx);容忍缺列。

    **排除研究员/dev 注入的测试被试**(`screening_json.dev == true`,devtools 的
    「跳过同意+筛查」)——它们不是真被试,绝不能进任何分析(保真/所有权/TOST/功效/
    试测健康)。此前无过滤,dev 走查行会静默污染毕业数据(深度评审 2026-07-19,#20)。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        trials = pd.read_sql("SELECT * FROM trials", con)
        quest = pd.read_sql("SELECT * FROM questionnaires", con)
        parts = pd.read_sql(
            "SELECT id, lang, seq, status, screening_json, final_survey_json,"
            " attention_ok, attention_raw FROM participants", con)
    finally:
        con.close()
    keep_ids = included_participants(db_path)
    n_before = trials["participant_id"].nunique()
    trials = trials[trials["participant_id"].isin(keep_ids)]
    n_after = trials["participant_id"].nunique()
    if n_after < n_before:
        # 试测早期没人走完 3 轮时会一个不剩 —— 那时的空结果必须与「库里本来就没数据」
        # 区分开,否则看到「N=0」的人会以为埋点坏了,而其实是纳入规则在正常工作。
        print(f"    纳入规则:{n_before} 人中排除 {n_before - n_after} 人"
              f"(dev 测试被试,或未走完 {config.N_ROUNDS} 轮)"
              + ("  ⚠️ 全部被排除 —— 库里有数据,但还没有人走完全部轮次"
                 if n_after == 0 else ""))
    # avoid column collisions on merge (id/created_at exist in both)
    quest = quest.drop(columns=[c for c in ("id", "created_at") if c in quest], errors="ignore")
    df = trials.merge(quest, on=["participant_id", "round_idx"], how="left",
                      suffixes=("", "_q"))
    # 问卷 ↔ 终稿错配(2026-09-02 起问卷记 trial_id):两个会话各自 INSERT OR REPLACE 过同一轮
    # 时,问卷评的可能不是现行那份终稿。以前这种行会静默进主终点。这里标出来并大声说。
    if "trial_id" in df.columns:
        df["q_trial_mismatch"] = df["trial_id"].notna() & (df["trial_id"] != df["id"])
        n_mis = int(df["q_trial_mismatch"].sum())
        if n_mis:
            print(f"    ⚠️ {n_mis} 行问卷评的不是现行终稿(trial_id 不一致,另一会话重做过这一轮)"
                  " —— per_trial 带出 q_trial_mismatch 列,主分析前须决定剔除或改用被评的那版")
    else:
        df["q_trial_mismatch"] = False
    # Participant language (ja/zh/en, picked once on the consent page). Everything
    # downstream is language-dependent — prompts, topic scenarios, the shot parser,
    # the scales and the embedding fidelity Δ — so analyses must be able to split or
    # filter on it, and the report must state the language mix. Formal study is ja;
    # any non-ja row is a researcher test or an off-protocol session.
    # 2026-09-01 拍板 2.1:主分析人群必须进得了管线。此前 v3 只带出 lang,于是
    # 「主分析 = novice 子集」(B1)在分析侧无从筛选,stats 实际跑的是全样本。
    # novice **每次从 screening_json 的原始 5 项重算**(prereg.is_novice),不信任
    # 入库时写下的布尔 —— 定义若在冻结前微调,旧行的布尔就是按旧定义算的。
    # 同时带出 status / seq:status 用于纳入规则(离脱者不进分析),
    # seq 用于核对拉丁方平衡。
    pmeta = parts.rename(columns={"id": "participant_id"}).copy()
    scr = pmeta["screening_json"].map(lambda x: _loads(x, {}))
    pmeta["novice"] = scr.map(prereg.is_novice)
    for k in prereg.NOVICE_CRITERION_FIELDS:      # 5 个原始子项,便于分报「哪一项筛掉了人」
        pmeta[f"nv_{k}"] = scr.map(lambda d, _k=k: prereg.novice_criteria(d)[_k])
    # 2026-09-02 新增的四项测量必须进得了管线,否则就是「收了但没人读」——
    # 这个项目已经因为同一种病吃过三次亏(NOVICE_DEF / DECISION_BRANCHES / 纳入规则)。
    # script_confidence 是自我效能(所有权/主导感的调节量);总问卷的三条是跨轮的
    # 收敛证据与操纵察觉。全部**探索性**,要当确证得先写进 prereg 与 04。
    pmeta["script_confidence"] = scr.map(lambda d: d.get("script_confidence"))
    fs = pmeta["final_survey_json"].map(lambda x: _loads(x, {}))
    for src, dst in (("pref_round", "fs_pref_round"), ("reuse_round", "fs_reuse_round"),
                     ("closest_round", "fs_closest_round"), ("effort_round", "fs_effort_round"),
                     ("overall_sat", "fs_overall_sat"), ("noticed_idx", "fs_noticed_idx"),
                     ("comment", "fs_comment")):
        pmeta[dst] = fs.map(lambda d, _k=src: d.get(_k))
    # attention_ok / attention_raw:docs/paper/04 §敏感性分析写着「剔除 attention 失败」,但此前
    # 全库没有任何代码读这两列 —— 先让它们进 CSV;剔不剔、怎么剔是冻结口径,不在这里悄悄做。
    keep = ["participant_id", "lang", "seq", "status", "novice", "script_confidence",
            "attention_ok", "attention_raw",
            "fs_pref_round", "fs_reuse_round", "fs_closest_round", "fs_effort_round",
            "fs_overall_sat", "fs_noticed_idx", "fs_comment"] + \
           [f"nv_{k}" for k in prereg.NOVICE_CRITERION_FIELDS]
    df = df.merge(pmeta[keep], on="participant_id", how="left")
    df["lang"] = df["lang"].fillna("ja")
    df["novice"] = df["novice"].fillna(False).astype(bool)

    # 跨轮强制选择问的是「哪一轮」,但分析要的是「哪个条件」—— 轮次↔条件的映射
    # 每个被试都不同(拉丁方),所以在这里就地翻译好,免得每个用它的人各写一遍。
    cond_by = {(int(r.participant_id), int(r.round_idx)): r.condition
               for r in df.itertuples() if pd.notna(r.condition)}
    for src, dst in (("fs_pref_round", "fs_pref_cond"), ("fs_reuse_round", "fs_reuse_cond"),
                     ("fs_closest_round", "fs_closest_cond"), ("fs_effort_round", "fs_effort_cond")):
        df[dst] = [cond_by.get((int(pid), int(rd))) if pd.notna(rd) else None
                   for pid, rd in zip(df["participant_id"], df[src])]
    return df


def _loads(x, default):
    if not isinstance(x, str) or not x.strip():
        return default
    try:
        return json.loads(x)
    except (ValueError, TypeError):
        return default


def _num(x):
    return x if isinstance(x, (int, float)) and not pd.isna(x) else np.nan


def _text(x) -> str:
    """文本列 → str;非字符串(含缺失)一律记空串。

    SQLite 的 NULL 读进 pandas 是 float('nan'),而 `nan or ""` **仍是 nan**
    (bool(nan) 为 True)→ 传给 core/shots.py 的 .strip() 就抛 AttributeError。
    半截行是真实存在的(scripts/dev_smoke_e2e.py 会写出缺终稿/缺版本历史的行),
    所有吃文本列的地方都必须过这个函数,不能再写 `<col> or ""`。
    """
    return x if isinstance(x, str) else ""


# ---------------- per-trial deterministic metrics ----------------

_DUR_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
SPEC_TOL_SEC = 1.0   # 总时长容差(秒);用户 2026-08-03 拍板 ±1


def _duration_sec(raw) -> float:
    """分镜时长字段 → 秒数。解析器给的是原文(「5秒」/「5」/「Duration: 5 sec」),
    取其中第一个数;取不到记 NaN(= 无法核验,不是 0 秒)。"""
    m = _DUR_NUM_RE.search(raw) if isinstance(raw, str) else None
    return float(m.group()) if m else np.nan


def structural_legs(final_output: str, total_seconds: float | None = None) -> dict:
    """任务规格符合度(客观质量下界):镜数、字段齐全率、是否达标。

    `spec_ok` = **「15s 且 3 镜」达标**(docs/paper/03 §4 的第三个成分)。此前只判镜数,
    总时长从没核验过——时长字段一直只被 field_completeness 当作「在不在」来数,
    数值从没取过(用户 2026-08-03 拍板补上)。目标秒数取自题目的 `total_seconds`
    (不硬编码 15,题目表里就是这个字段),容差 ±SPEC_TOL_SEC。
    时长解析不出来 → 无法核验 → 记 0(客观**下界**的一贯口径:不能证明达标就不算达标),
    而不是记缺失——否则解析越烂、分数越高。
    `shots_ok`(仅镜数)保留作描述性列,不再是 H4 的成分。"""
    shots = parse_shots(_text(final_output))
    n = len(shots)
    if not n:
        return {"n_shots": 0, "field_completeness": np.nan, "parse_ok": 0,
                "shots_ok": 0, "dur_total": np.nan, "spec_ok": 0}
    comp = np.mean([sum(bool(s.get(f)) for f in _SHOT_FIELDS) / len(_SHOT_FIELDS)
                    for s in shots])
    durs = [_duration_sec(s.get("duration")) for s in shots]
    dur_total = float(np.sum(durs)) if not any(np.isnan(d) for d in durs) else np.nan
    target = total_seconds if total_seconds else None
    dur_ok = (target is not None and not np.isnan(dur_total)
              and abs(dur_total - target) <= SPEC_TOL_SEC)
    return {"n_shots": n, "field_completeness": float(comp),
            "parse_ok": int(any(s.get("visual") for s in shots)),
            "shots_ok": int(n == 3),
            "dur_total": dur_total,
            "spec_ok": int(n == 3 and dur_ok)}


def shot_fidelity(shot_annotations_json) -> dict:
    """逐镜头保真:mine / ai_ok / ai_against 占比(自评但离散、逐镜头)。

    ⚠️ 粒度分层:终稿 parse_shots 失败时 views/questionnaire.py 退化为「整稿一个
    标签」并存 [{"shot": 0, tag}],此时 mine_ratio 只能取 0/1,与 3 镜头行的
    0/.33/.67/1 不同量纲(混入主终点=测量粒度混淆)。不丢行、不改问卷,只导出
    n_shots_tagged / whole_script_fallback,供下游分层与敏感性分析(剔除
    whole_script_fallback==1 后主终点是否稳)。

    ⚠️ 缺问卷 ≠ 标注不可用:trials ⟕ questionnaires 是左连接,该轮问卷还没提交时
    标注列整个缺失 —— 此时 n_shots_tagged / whole_script_fallback 记 **NaN**(没有
    这份数据,与模块开头「缺问卷 → NaN」同口径);**0 专表**「问卷已交但一个合法标签
    都没有」(标注为空 / tag 非法 = 有数据但不可用)。下游筛「标注不可用」的行用
    `n_shots_tagged == 0`,别用 `fillna(0)` 把未提交的轮一起卷进来。
    """
    if not _text(shot_annotations_json).strip():   # 问卷未提交 → 无数据,不是 0
        return {"n_shots_tagged": np.nan, "whole_script_fallback": np.nan,
                **{f"{t}_ratio": np.nan for t in _TAGS}, "not_against": np.nan}   # 键集必须与下面两支一致
    ann = _loads(shot_annotations_json, [])
    tags = [a.get("tag") for a in ann if isinstance(a, dict) and a.get("tag") in _TAGS]
    gran = {
        "n_shots_tagged": len(tags),
        "whole_script_fallback": int(len(ann) == 1 and isinstance(ann[0], dict)
                                     and ann[0].get("shot") == 0),
    }
    if not tags:
        return {**gran, **{f"{t}_ratio": np.nan for t in _TAGS}, "not_against": np.nan}
    ratios = {f"{t}_ratio": tags.count(t) / len(tags) for t in _TAGS}
    # 2026-09-01 拍板 2.5:保真主复合的第三腿 = **意图一致性**,不是贡献量。
    # not_against = 没有违背本意的镜头占比(mine + ai_ok)。与 mine_ratio 的区别:
    # 一份全由 AI 写、但每一镜都合我意的稿子 not_against=1 而 mine_ratio=0 —— 保真高、
    # 贡献低,这正是「E 提高的是保真还是贡献量」要能分开的那一维。
    # mine_ratio 仍导出,降为描述性/机制变量(它与 own3 同构念,见 stats._FIDELITY_SUBJ_LEGS)。
    return {**gran, **ratios, "not_against": 1.0 - ratios["ai_against_ratio"]}


def guidance_dose(guidance_json) -> dict:
    """E 引导剂量(docs/paper/03 §7「E 引导剂量」)→ H5 剂量-反应(docs/paper/04
    §3.1-4)的自变量。

    guidance_json = {"rounds":[{round, source, items:[{dimension, question,
    options, chosen, is_custom, ai_decided, fallback}], draft_snapshot_ref?}]}。
    落库的 item 必然已作答(views/guidance.py `_answered` 门禁),故自填率/AI 代答率
    以 item 数为分母。C/D 无 guidance_json → 全 NaN。

    ⚠️ fallback item 会把剂量顶到最高:引导 LLM 失败时 views/guidance.py 退化成一道
    无选项的自由填空(dimension="fallback"),而 `_answered` 对无选项题要求打字 → 这种
    item 必然 is_custom=True、ai_decided=False,于是**最差的一次引导反而给出最大的 H5
    剂量**(整轮退化 → g_custom_rate=1.0)。这里只如实导出 g_fallback_rate(fallback
    item 占比),**不动 g_custom_rate 的分母** —— fallback 到底算不算剂量(剔除 / 当
    协变量 / 照算)是研究员的口径决定,尚未拍板。
    """
    rounds = [r for r in (_loads(guidance_json, {}).get("rounds") or []) if isinstance(r, dict)]
    items = [it for r in rounds for it in (r.get("items") or []) if isinstance(it, dict)]
    if not items:
        return {"g_custom_rate": np.nan, "g_ai_decided_rate": np.nan,
                "g_fallback_rate": np.nan, "g_n_questions": np.nan, "g_n_rounds": np.nan}
    return {
        "g_custom_rate": sum(bool(it.get("is_custom")) for it in items) / len(items),
        "g_ai_decided_rate": sum(bool(it.get("ai_decided")) for it in items) / len(items),
        "g_fallback_rate": sum(bool(it.get("fallback")) for it in items) / len(items),
        "g_n_questions": len(items),
        "g_n_rounds": len(rounds),
    }


def version_evo(script_versions, final_output: str) -> dict:
    """版本演化:终稿 vs 首个 AI 版的相似度(改了多少)、AI/人改版数。"""
    vs = _loads(script_versions, [])
    ai_texts = [v.get("text", "") for v in vs if v.get("author") == "ai"]
    n_ai = len(ai_texts)
    n_user = sum(1 for v in vs if v.get("author") == "user_edit")
    first_ai = ai_texts[0] if ai_texts else ""
    final = _text(final_output) or (_text(vs[-1].get("text")) if vs else "")
    if first_ai and final:
        ratio = difflib.SequenceMatcher(
            None, strip_format(first_ai), strip_format(final)).ratio()
    else:
        ratio = np.nan
    return {"n_ai_versions": n_ai, "n_user_versions": n_user,
            "final_vs_firstai_sim": ratio}  # 1=没改, 低=改得多


def subjective_metrics(row: pd.Series) -> dict:
    own = _loads(row.get("ownership_json"), {})
    soa = _loads(row.get("soa_json"), {})
    tlx = _loads(row.get("tlx_json"), {})
    own_vals = [own.get(f"own{i}") for i in (1, 2, 3) if own.get(f"own{i}") is not None]
    soa_vals = [soa.get(f"soa{i}") for i in (1, 2) if soa.get(f"soa{i}") is not None]
    # straight-lining careless-response flag: every item in the Likert block
    # (own1-3 + soa1-2 + tlx1) identical, with ≥4 items answered (deep-review #31).
    block = own_vals + soa_vals + ([tlx.get("tlx1")] if tlx.get("tlx1") is not None else [])
    straightline = int(len(block) >= 4 and len(set(block)) == 1)
    ai_q_best = _loads(row.get("ai_q_best_json"), None)
    return {
        "own_mean": float(np.mean(own_vals)) if own_vals else np.nan,
        "soa_mean": float(np.mean(soa_vals)) if soa_vals else np.nan,
        # 以下三列此前「收了但没人读」(tlx1 只被拿来算 straightline)。全部描述性/探索性。
        "tlx1": _num(tlx.get("tlx1")),
        "ai_q_amount": _num(row.get("ai_q_amount")),          # E only:1 太少 · 4 刚好 · 7 太多
        "n_ai_q_helpful": (len(ai_q_best) if isinstance(ai_q_best, list) else np.nan),  # E only;NaN=没问
        "violation": _num(row.get("intent_violation")),
        "imagine": _num(row.get("imagine_match")),
        "satisfaction": _num(row.get("satisfaction")),
        "ai_q_quality": _num(row.get("ai_q_quality")),  # E only
        "straightline": straightline,
    }


def behavioral_metrics(row: pd.Series) -> dict:
    """努力再分配:事前投入(E 引导答题) vs 事后返工;三口径并报(docs/paper/03 §5.1)。

    缺失 ≠ 0:C/D 没有引导步,pre 是**设计上的 0**;E 的 t_pregen 缺失是数据缺失,保留 NaN
    (以前记 0 → 进 H5 剂量复合后伪装成「剂量最低」的极端点);t_postgen 缺失时 total 也是 NaN,
    不能把缺失轮当成「0 秒投入」拉低次要终点的均值。"""
    pre = _num(row.get("t_pregen"))
    post = _num(row.get("t_postgen"))
    pre0 = 0.0 if (row.get("condition") in ("C", "D") and pd.isna(pre)) else pre
    total = np.nan if (pd.isna(pre0) or pd.isna(post)) else pre0 + post
    return {
        "pre_investment": pre0,
        "post_investment": post,
        "total_investment": total,
        "n_ai_rounds": _num(row.get("n_ai_rounds")),
        "n_hand_edits": _num(row.get("n_hand_edits")),
        "hand_edit_chars": _num(row.get("hand_edit_chars")),
        "t_total": _num(row.get("t_total")),
    }


def per_trial(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        m = {
            "participant_id": r.get("participant_id"),
            "round_idx": r.get("round_idx"),
            "condition": r.get("condition"),
            "topic": _topic_title(r.get("topic_json")),
            # 被试在同意页选的语言(ja/zh/en)。prompt / 题目情境 / 分镜解析 / 量表 /
            # embedding 保真Δ 全都随语言变,故必须能按它切分或过滤,且报告要写明语言构成。
            "lang": r.get("lang") or "ja",
            # 主分析人群与纳入/平衡列(2026-09-01 拍板 2.1)。novice 由 load() 从
            # screening_json 的原始 5 项重算;nv_* 是 5 个子项,用来分报「哪一项筛掉了人」
            # (试测时若 novice 占比不足,要靠它判断该收紧招募还是放宽到 4-of-5)。
            "novice": bool(r.get("novice")),
            "status": r.get("status"),
            "seq": r.get("seq"),
            # ⚠️ 不能写 bool(r.get(...)):merge 没匹配上的孤儿 trial(participants 里
            # 没有该 id)这些列是 NaN,而 **bool(nan) 恒为 True** —— 会把 5 个子项
            # 全报成「满足」,与同一行 novice=False 直接矛盾。缺就记 NaN。
            **{f"nv_{k}": (np.nan if pd.isna(r.get(f"nv_{k}")) else bool(r.get(f"nv_{k}")))
               for k in prereg.NOVICE_CRITERION_FIELDS},
            # 2026-09-02 新增的四项测量。它们是**逐被试**的量,在逐 trial 表里会重复
            # 三行 —— 分析时按 participant_id 去重即可;放在这里是为了让它们真的
            # 出现在 CSV 里,而不是止步于 load()(「收了但没人读」已经栽过三次)。
            "script_confidence": r.get("script_confidence"),
            "attention_ok": r.get("attention_ok"),
            "attention_raw": r.get("attention_raw"),
            "q_trial_mismatch": bool(r.get("q_trial_mismatch")),
            **{k: r.get(k) for k in ("fs_pref_round", "fs_reuse_round", "fs_closest_round",
                                     "fs_effort_round", "fs_overall_sat", "fs_noticed_idx", "fs_comment",
                                     "fs_pref_cond", "fs_reuse_cond", "fs_closest_cond",
                                     "fs_effort_cond")},
        }
        m.update(structural_legs(r.get("final_output"), _topic_seconds(r.get("topic_json"))))
        m.update(shot_fidelity(r.get("shot_annotations_json")))
        m.update(guidance_dose(r.get("guidance_json")))
        m.update(version_evo(r.get("script_versions"), r.get("final_output")))
        m.update(subjective_metrics(r))
        m.update(behavioral_metrics(r))
        rows.append(m)
    return pd.DataFrame(rows)


def _topic_seconds(topic_json) -> float | None:
    """题目要求的总时长(秒);缺失/脏值 → None(spec_ok 随之判 0,无法核验即不算达标)。"""
    t = _loads(topic_json, {})
    try:
        return float(t.get("total_seconds")) if isinstance(t, dict) else None
    except (TypeError, ValueError):
        return None


def _topic_title(topic_json) -> str:
    t = _loads(topic_json, {})
    title = t.get("title", "") if isinstance(t, dict) else ""
    if isinstance(title, dict):
        return title.get("ja") or title.get("zh") or ""
    return title or ""


# ---------------- condition × topic diversity (group-level) ----------------

def diversity_by_group(df: pd.DataFrame) -> pd.DataFrame:
    """同题内、按条件分组算成稿多样性(越同质 → CR 高 / distinct 低)。
    回答'哪种流水线让新手产出更同质化'(docs/paper/03 §4「多样性」)。"""
    out = []
    df = df.copy()
    df["topic"] = df["topic_json"].map(_topic_title)
    for (cond, topic), g in df.groupby(["condition", "topic"]):
        finals = [strip_format(_text(x)) for x in g["final_output"].tolist()]
        finals = [x for x in finals if x]
        if len(finals) < 2:
            continue
        out.append({
            "condition": cond, "topic": topic, "n": len(finals),
            "gzip_cr": textstats.gzip_cr(finals),
            "distinct2": textstats.distinct_n(finals, 2),
            "self_rep4": textstats.self_repetition(finals, 4),
        })
    return pd.DataFrame(out)


# ---------------- CLI ----------------

_SUMMARY_COLS = [
    "parse_ok", "field_completeness", "shots_ok", "spec_ok", "dur_total",
    "own_mean", "soa_mean", "satisfaction", "imagine", "violation", "ai_q_quality",
    "tlx1", "ai_q_amount", "n_ai_q_helpful",
    "mine_ratio", "ai_against_ratio", "not_against", "final_vs_firstai_sim",
    "n_shots_tagged", "whole_script_fallback",
    "g_custom_rate", "g_ai_decided_rate", "g_fallback_rate", "g_n_questions", "g_n_rounds",
    "pre_investment", "post_investment", "total_investment",
    "n_ai_rounds", "hand_edit_chars", "straightline",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="A6 v3 确定性指标(无 API)")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "analysis" / "v3_per_trial.csv")
    args = ap.parse_args()

    raw = load(args.db)
    pt = per_trial(raw)
    if pt.empty:  # 试测第一位被试提交前:空库/只有 dev 行 → 干净退出,别抛栈
        print(f"{args.db} 里还没有 trials(N=0),无可算指标。")
        return
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pt.to_csv(args.out, index=False)

    print(f"载入 {len(raw)} 行 trials;逐 trial 指标 {len(pt)} 行 → {args.out}\n")
    # 人群构成必须在抬头就看见 —— 主分析跑的是 novice 子集(B1),它的 N 往往远小于全样本,
    # 而此前整条管线里没有任何一处会把这个数字说出来。
    n_p = pt["participant_id"].nunique()
    n_nv = pt.loc[pt["novice"], "participant_id"].nunique()
    share = (n_nv / n_p) if n_p else float("nan")
    print(f"=== 人群:全样本 {n_p} 人 / novice 子集 {n_nv} 人({share:.0%})===")
    print(f"    主分析人群 = novice(prereg.NOVICE_MIN_CRITERIA="
          f"{prereg.NOVICE_MIN_CRITERIA}/5,B1);全样本作稳健性")
    if n_p and share < prereg.NOVICE_SHARE_YELLOW:
        print(f"    ⚠️ novice 占比 <{prereg.NOVICE_SHARE_YELLOW:.0%} = pilot_check ③ 🔴"
              f" —— 需收紧招募或启用 4-of-5 退路(B1)")
    # 哪一项把人筛掉了 —— 试测时据此判断该改招募还是该放宽定义
    # 用 == False 而不是 ~:nv_* 是「np.nan if 缺项 else bool」生成的(为了避开
    # bool(NaN) 恒 True),只要有一位被试缺了任一子项,整列就退成 object dtype,
    # 而 ~ 对 object 列会抛 TypeError —— 一条残缺的 screening 行就能让整条分析链崩掉。
    # == False 天然把 NaN 排除在外,正是「未满足」应有的语义(缺项 ≠ 不满足)。
    sub = pt.drop_duplicates("participant_id")
    fails = {k: int((sub[f"nv_{k}"] == False).sum())  # noqa: E712 — NaN 安全,不能改 `not`
             for k in prereg.NOVICE_CRITERION_FIELDS}
    print(f"    未满足人数(按子项): {fails}\n")
    if "lang" in pt.columns:
        mix = pt.groupby("lang")["participant_id"].nunique().to_dict()
        note = "" if set(mix) <= {"ja"} else "   ⚠️ 非 ja 会话(研究员测试/脱离协议)——分析前确认是否剔除"
        print(f"=== 被试语言构成(人数)=== {mix}{note}\n")
    have = [c for c in _SUMMARY_COLS if c in pt.columns]
    print("=== 按条件均值(核心对比 C/D/E)===")
    with pd.option_context("display.width", 200, "display.max_columns", 40,
                           "display.float_format", lambda x: f"{x:.2f}"):
        print(pt.groupby("condition")[have].mean(numeric_only=True).T)
        print("\n=== 条件×题目 多样性(CR 高=更同质)===")
        div = diversity_by_group(raw)
        print(div.to_string(index=False) if len(div) else "(每组 <2 稿,略)")
    print("\n待补(需真数据/API):embedding 相对基线保真 Δ、LMM/TOST、图表。")


if __name__ == "__main__":
    main()
