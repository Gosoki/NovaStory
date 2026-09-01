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
from core.shots import parse_shots, strip_format  # noqa: E402

DEFAULT_DB = ROOT / "data" / "novastory.db"
_SHOT_FIELDS = ("shot_type", "visual", "audio", "duration")
_TAGS = ("mine", "ai_ok", "ai_against")


# ---------------- load ----------------

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
            "SELECT id, lang, seq, status, screening_json FROM participants", con)
    finally:
        con.close()
    dev_ids = {int(r.id) for r in parts.itertuples()
               if _loads(r.screening_json, {}).get("dev")}
    if dev_ids:
        trials = trials[~trials["participant_id"].isin(dev_ids)]
    # avoid column collisions on merge (id/created_at exist in both)
    quest = quest.drop(columns=[c for c in ("id", "created_at") if c in quest], errors="ignore")
    df = trials.merge(quest, on=["participant_id", "round_idx"], how="left",
                      suffixes=("", "_q"))
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
    for k in prereg.NOVICE_CRITERIA:      # 5 个原始子项,便于分报「哪一项筛掉了人」
        pmeta[f"nv_{k}"] = scr.map(lambda d, _k=k: prereg.novice_criteria(d)[_k])
    keep = ["participant_id", "lang", "seq", "status", "novice"] + \
           [f"nv_{k}" for k in prereg.NOVICE_CRITERIA]
    df = df.merge(pmeta[keep], on="participant_id", how="left")
    df["lang"] = df["lang"].fillna("ja")
    df["novice"] = df["novice"].fillna(False).astype(bool)
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


def structural(final_output: str, total_seconds: float | None = None) -> dict:
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
                **{f"{t}_ratio": np.nan for t in _TAGS}}
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


def subjective(row: pd.Series) -> dict:
    own = _loads(row.get("ownership_json"), {})
    soa = _loads(row.get("soa_json"), {})
    tlx = _loads(row.get("tlx_json"), {})
    own_vals = [own.get(f"own{i}") for i in (1, 2, 3) if own.get(f"own{i}") is not None]
    soa_vals = [soa.get(f"soa{i}") for i in (1, 2) if soa.get(f"soa{i}") is not None]
    # straight-lining careless-response flag: every item in the Likert block
    # (own1-3 + soa1-2 + tlx1) identical, with ≥4 items answered (deep-review #31).
    block = own_vals + soa_vals + ([tlx.get("tlx1")] if tlx.get("tlx1") is not None else [])
    straightline = int(len(block) >= 4 and len(set(block)) == 1)
    return {
        "own_mean": float(np.mean(own_vals)) if own_vals else np.nan,
        "soa_mean": float(np.mean(soa_vals)) if soa_vals else np.nan,
        "violation": _num(row.get("intent_violation")),
        "imagine": _num(row.get("imagine_match")),
        "satisfaction": _num(row.get("satisfaction")),
        "ai_q_quality": _num(row.get("ai_q_quality")),  # E only
        "straightline": straightline,
    }


def behavioral(row: pd.Series) -> dict:
    """努力再分配:事前投入(E 引导答题) vs 事后返工;三口径并报(docs/paper/03 §5.1)。"""
    pre = _num(row.get("t_pregen"))
    post = _num(row.get("t_postgen"))
    pre0 = 0.0 if pd.isna(pre) else pre        # C/D 无事前引导 → 记 0
    total = pre0 + (0.0 if pd.isna(post) else post)
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
            **{f"nv_{k}": bool(r.get(f"nv_{k}")) for k in prereg.NOVICE_CRITERIA},
        }
        m.update(structural(r.get("final_output"), _topic_seconds(r.get("topic_json"))))
        m.update(shot_fidelity(r.get("shot_annotations_json")))
        m.update(guidance_dose(r.get("guidance_json")))
        m.update(version_evo(r.get("script_versions"), r.get("final_output")))
        m.update(subjective(r))
        m.update(behavioral(r))
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
    fails = {k: int((~pt.drop_duplicates("participant_id")[f"nv_{k}"]).sum())
             for k in prereg.NOVICE_CRITERIA}
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
