#!/usr/bin/env python
"""A6: embedding 相对基线的意图保真 Δ(paper/14 §5 保真主力)。

Δ = cos(创意, 终稿) − cos(创意, 同题机器基线质心)
   把'纯 AI 本来就有多贴'当零点,只主张人类介入带来的增量,避免绝对余弦陷阱
   (Steck 2024:cos 不等于语义相似)。多模型稳健性建议 ≥3 个,此处先接 OpenAI
   text-embedding-3,本地 e5(需 torch)留作可选副模型。

需要:OpenAI key(secrets 里的 openai.com 配置)+ 被试语言的机器基线(先跑 make baseline;
      ja 在 data/baseline/,其他语言在 data/baseline/<lang>/,见 baseline_dir)。
产出:把两列并入 data/analysis/v3_per_trial.csv(2026-09-23 决策:「意图」两种定义都算都报):
  embed_fidelity         锚 = 仅 intent_statement(原定义)→ 供 stats.py 进保真复合
  embed_fidelity_guided  锚 = intent_statement + E 的引导答案 → 只作敏感性,**不进保真复合**

用法: .venv/bin/python analysis/embed.py            # 计算并合入(每位被试比自己语言的基线)
      .venv/bin/python analysis/embed.py --lang zh  # 只算 zh 被试,其余记 NaN
      .venv/bin/python analysis/embed.py --selftest # 纯数学自测(无 API)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tomllib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import prereg  # noqa: E402
from core.shots import strip_format  # noqa: E402

DATA = ROOT / "data"
DB = DATA / "novastory.db"
BASELINE = DATA / "baseline"
CSV = DATA / "analysis" / "v3_per_trial.csv"
CACHE = DATA / "analysis" / "emb_cache.json"
SECRETS = ROOT / ".streamlit" / "secrets.toml"
_MIN_BASELINE = prereg.MIN_BASELINE_PER_TOPIC  # 质心的机器稿下限:更少则「纯 AI 本来有多贴」这个零点全是抽样噪声
# data/baseline/ 根目录那份基线的语言(历史布局;deploy_check.embed_lang 读它)。
# 其他语言的基线放 data/baseline/<lang>/(baseline_dir);每位被试只和**自己语言**的基线比。
BASELINE_LANG = "ja"
# 正式队列的语言(ja=日本队列、zh=中国队列,docs/paper/03):缺同语言基线 → 硬失败。
# 其余语言(en = 研究员测试 / 脱离协议)缺基线时照旧:不发 API、Δ 记 NaN、大声说。
COHORT_LANGS = ("ja", "zh")
# 意图定义②(敏感性):锚 = intent_statement + E 引导答案。stats 按列名精确认 embed_fidelity
# (定义①,原口径),所以这一列不会进保真复合。两种定义方向可能相反,都必须报(docs/paper/10)。
GUIDED_COL = "embed_fidelity_guided"


def baseline_dir(lang: str) -> Path:
    """某语言的机器基线目录。ja 沿用历史位置 data/baseline/(deploy_check 与文档都指这里),
    其他语言放 data/baseline/<lang>/ —— 分目录而不是同名文件,两种语言的质心在结构上就串不了。"""
    return BASELINE if lang == BASELINE_LANG else BASELINE / lang


def guided_intent(intent: str, guidance_json) -> str:
    """意图定义②的锚文本 = intent_statement + E 引导里被试确定下来的答案
    (各轮各题 `chosen` 非空者:选中的选项文字或自填文字;「交给 AI」的题 chosen 为空,不算)。
    C/D 没有 guidance_json → 原样返回 intent,与定义①完全相同。"""
    g = json.loads(guidance_json) if isinstance(guidance_json, str) and guidance_json.strip() else {}
    answers = [(it.get("chosen") or "").strip()
               for r in (g.get("rounds") or []) if isinstance(r, dict)
               for it in (r.get("items") or []) if isinstance(it, dict)]
    return "\n".join([intent, *[a for a in answers if a]])


# ---------------- pure math(可单测)----------------

def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def fidelity_delta(intent: np.ndarray, final: np.ndarray,
                   baseline: np.ndarray) -> float:
    """Δ = cos(创意,终稿) − cos(创意,基线质心)。baseline = (K, dim) 机器基线向量。"""
    centroid = np.asarray(baseline, dtype=float).mean(axis=0)
    return cosine(intent, final) - cosine(intent, centroid)


# ---------------- OpenAI backend(带磁盘缓存)----------------

def _openai_cfg() -> dict:
    cfgs = tomllib.loads(SECRETS.read_text()).get("api_configs", [])
    for c in cfgs:
        if "openai.com" in c.get("base_url", "") and c.get("api_key"):
            return c
    raise SystemExit("secrets 里没有可用的 OpenAI 配置(embedding 需要)。")


class Embedder:
    def __init__(self, model: str = "text-embedding-3-small"):
        from openai import OpenAI
        cfg = _openai_cfg()
        self.client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
        self.model = model
        self.cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    def _key(self, text: str) -> str:
        return hashlib.sha1(f"{self.model}\n{text}".encode()).hexdigest()

    def embed(self, text: str) -> np.ndarray:
        text = strip_format(text or "").strip() or "(empty)"
        k = self._key(text)
        if k not in self.cache:
            v = self.client.embeddings.create(model=self.model, input=text).data[0].embedding
            self.cache[k] = v
        return np.asarray(self.cache[k], dtype=float)

    def flush(self) -> None:
        """原子写(temp + rename):写一半崩掉不会留下损坏的缓存,下次启动 json.loads 才不会炸。"""
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache))
        tmp.replace(CACHE)


def _baseline_texts(topic_idx: int, topics: list, lang: str = BASELINE_LANG) -> list[str]:
    """读一题的 `lang` 机器基线,并校验这份基线确实属于这道题、这种语言。

    topic{i}.jsonl 只按 topics.json 的顺序命名:题目一旦重排/改写,序号就会把基线
    安到别的题上(错配是静默的)。baseline_gen 每行记了 topic_idx,缺省又把该题
    scenario 当 seed,故用 seed 反查题目身份;基线缺失/太少一律硬失败,不返回 NaN。"""
    p = baseline_dir(lang) / f"topic{topic_idx}.jsonl"
    redo = f"删掉 {p.parent}/topic*.jsonl 重跑 make baseline BASELINE_LANG={lang}"
    if not p.exists():
        raise SystemExit(f"缺少 {lang} 机器基线 {p} —— 先跑: make baseline BASELINE_LANG={lang}")
    # 反查题目身份用与 baseline_gen 相同语言(= 这份基线的语言 lang)的情境
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(recs) < _MIN_BASELINE:
        raise SystemExit(f"{p} 只有 {len(recs)} 份基线(<{_MIN_BASELINE}),质心不可靠 —— "
                         f"重跑: make baseline BASELINE_LANG={lang}")
    bad = {r.get("topic_idx") for r in recs} - {topic_idx}
    if bad:
        raise SystemExit(f"{p} 内记录的 topic_idx={bad} 与文件名不符 —— 基线与题目错配,{redo}")
    # 旧版 baseline_gen 不分目录,--lang zh 会写进根目录:别语言的稿子混进质心,而 seed 检查
    # 对这种情况只会打一句警告(下面那条 print)。行里记了 lang 就对上;没记的旧行只能信任。
    wrong = {r.get("lang") for r in recs} - {lang, None}
    if wrong:
        raise SystemExit(f"{p} 里有 lang={sorted(wrong)} 的稿子,这里只能放 {lang} 基线 —— {redo}")
    from core import prompts as _prompts  # noqa: PLC0415 — avoid a UI import at module load
    scen = [_prompts.scenario_text(t, lang) for t in topics]
    seed = (recs[0].get("seed") or "").strip()
    if seed and seed != scen[topic_idx]:
        if seed in scen:
            raise SystemExit(f"{p} 是用第 {scen.index(seed)} 题的情境生成的 —— topics.json 已被"
                             f"重排/改写,基线与题目错配,{redo}")
        # seed 对不上任何一题的情境 = 这份基线是用**旧口径**生成的。历史上有过两版:
        #   · 2026-09-02 前:seed 里还带着「以上の分岐はあくまで例です…」那句元指令;
        #   · 2026-09-06 前:seed = 情境 + 分支列表(choices)。
        # 现口径只发情境主干(见 prompts.scenario_text 的说明)。任何一版旧基线的
        # 「假装的用户创意」都与被试稿不同源,Δ 的零点量的不是同一件事 —— 必须硬失败,
        # 不能只警告一句。基线本来就必须与采数同模型重跑,不存在"将就用"的选项。
        if any(seed.startswith(x) for x in scen if x):
            raise SystemExit(
                f"{p.name} 的 seed 以第 {[i for i, x in enumerate(scen) if x and seed.startswith(x)][0]} "
                "题的情境开头但更长 —— 是 2026-09-06 口径变更**之前**生成的"
                "(seed 里含分支列表/元指令),Δ 的零点与被试稿不同源。"
                f"用正式模型{redo}。")
        print(f"⚠️ {p.name} 的 seed 不是任何题的情境(可能用了 --seeds-file):题目身份只能按"
              "文件序号信任,请确认 topics.json 自生成基线以来未改动。")
    return [r["text"] for r in recs]


def _check_baseline_model(topic_idxs, trial_models: set[str], lang: str = BASELINE_LANG) -> None:
    """`lang` 基线的生成模型必须与**该语言被试**实际用的生成模型一致。

    Δ = sim(创意,终稿) − sim(创意,**基线**质心),零点就是「纯 AI 会写成什么样」。基线若
    是另一个模型(或另一次快照)写的,这个零点量的就不是同一件事,Δ 失去意义——而且**事后
    补不回来**:正式数据一旦采完,当时的模型可能已经下线。baseline_gen 每行记了 model,
    trials 每行也记了 model,直接对上。不一致 → 硬失败,不产出看着能用的假 Δ。"""
    d = baseline_dir(lang)
    base_models = set()
    for i in topic_idxs:
        pth = d / f"topic{i}.jsonl"
        if not pth.exists():   # 存在性由 _baseline_texts 给出可读提示;这里别抢先抛裸 FileNotFoundError
            continue
        # 看**每一行**的 model:中途换配置续跑过的文件,只看首行检不出来
        for l in pth.read_text(encoding="utf-8").splitlines():
            if l.strip():
                base_models.add(json.loads(l).get("model") or "?")
    if len(base_models) > 1:
        raise SystemExit(f"{d}/ 里混了多个模型 {sorted(base_models)} —— "
                         f"同一批基线必须同模型,删掉 {d}/topic*.jsonl 重跑 make baseline BASELINE_LANG={lang}")
    if base_models and trial_models and base_models != trial_models:
        raise SystemExit(
            f"{lang} 基线模型 {sorted(base_models)} 与 {lang} 被试实际用的生成模型 {sorted(trial_models)} 不一致。\n"
            "   Δ 的零点(「纯 AI 本来就有多贴」)必须由**同一个模型**产生,否则 Δ 量的不是同一件事。\n"
            f"   处置:用正式采数的那个模型快照重跑 `make baseline BASELINE_LANG={lang}`(改 secrets 的 "
            "api_configs[0] 或 --config-index),再跑 make embed。")


def compute(only_lang: str | None = None) -> None:
    import pandas as pd
    if not CSV.exists():
        raise SystemExit("先跑 analysis/v3.py 生成 v3_per_trial.csv。")
    if not BASELINE.exists():
        raise SystemExit(f"缺少机器基线目录 {BASELINE} —— Δ 的零点就是它,先跑: make baseline")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    trials = pd.read_sql("SELECT participant_id, round_idx, intent_statement, final_output, "
                         "topic_json, model, guidance_json FROM trials", con)
    langs = pd.read_sql("SELECT id AS participant_id, lang FROM participants", con)
    con.close()

    # 纳入规则(同 analysis/v3):此前 embed **连 dev 过滤都没有** —— 研究员的测试稿会
    # 进质心、也会被送去 OpenAI 算 embedding。而同意书写着中止者的回答不用于分析,
    # embedding 正是「分析」里最实打实的那一步(还要把原文外发一次)。
    from analysis import v3 as _v3  # noqa: PLC0415 — 避免模块级循环依赖
    keep = _v3.included_participants(DB)
    n0 = trials["participant_id"].nunique()
    trials = trials[trials["participant_id"].isin(keep)]
    if trials["participant_id"].nunique() < n0:
        print(f"  纳入规则:{n0} 人中排除 "
              f"{n0 - trials['participant_id'].nunique()} 人(dev / 未走完全部轮次)")
    # 语言:每位被试只和**自己语言**的基线比(prompt / 题面 / 分镜模板全随语言变,跨语言的零点
    # 不可比)。正式队列(COHORT_LANGS)缺基线 → 由 _baseline_texts 硬失败;其余语言缺基线、
    # 或被 --lang 排除 → 不发 API、Δ 记 NaN、大声说。
    trials = trials.merge(langs, on="participant_id", how="left")
    trials["lang"] = trials["lang"].fillna(BASELINE_LANG)
    skip = (trials["lang"] != only_lang) if only_lang else pd.Series(False, index=trials.index)
    for lg in set(trials.loc[~skip, "lang"]):
        if lg not in COHORT_LANGS and not any(baseline_dir(lg).glob("topic*.jsonl")):
            skip |= trials["lang"] == lg
    for lg, n in trials[skip].groupby("lang")["participant_id"].nunique().items():
        why = f"不在 --lang {only_lang} 内" if only_lang and lg != only_lang else "无同语言基线(非正式队列)"
        print(f"  ⚠️ {n} 位 {lg} 被试{why},embed_fidelity 记 NaN")
    trials = trials[~skip]
    if trials.empty:
        raise SystemExit("没有任何被试可算 Δ(语言被 --lang 排除,或都没有同语言基线)。")

    # topic title → baseline index(按 topics.json 顺序,由 _baseline_texts 复核身份)。
    # 题名一律按 BASELINE_LANG 对:topic_json 三语齐全,题目身份与被试语言无关。
    from core import state as _state  # noqa: PLC0415 — 与被试同一份题库归一化(shot_seconds→total_seconds)
    topics = _state.load_topics()
    title2idx = {_state.topic_text(t, "title", BASELINE_LANG): i for i, t in enumerate(topics)}
    trials["topic_idx"] = [
        title2idx.get(_state.topic_text(json.loads(tj) if tj else {}, "title", BASELINE_LANG))
        for tj in trials["topic_json"]]
    used = sorted({int(i) for i in trials["topic_idx"].dropna().unique()})
    if not used:
        raise SystemExit("没有一条 trial 的题目能对上 topics.json(题面被改过?)——无法配基线。")
    # 先纯文件校验+读齐所有用到的基线,再花任何 API 调用。逐语言:先存在性/身份(可读提示),再模型一致性
    base_texts = {}
    for lg, g in trials.groupby("lang"):
        used_lg = sorted({int(i) for i in g["topic_idx"].dropna().unique()})
        base_texts[lg] = {i: _baseline_texts(i, topics, lg) for i in used_lg}
        _check_baseline_model(used_lg, {m for m in g["model"].dropna().unique() if str(m).strip()}, lg)

    emb = Embedder()
    base_vecs = {(lg, i): np.array([emb.embed(t) for t in ts])
                 for lg, per_topic in base_texts.items() for i, ts in per_topic.items()}
    deltas = []
    for _, r in trials.iterrows():
        ti = r["topic_idx"]
        if pd.isna(ti) or not r["intent_statement"] or not r["final_output"]:
            d = d_g = np.nan
        else:
            base, final = base_vecs[(r["lang"], int(ti))], emb.embed(r["final_output"])
            d = fidelity_delta(emb.embed(r["intent_statement"]), final, base)
            # 定义②:C/D 的锚文本与定义①相同 → 命中缓存、d_g == d,不多花调用
            d_g = fidelity_delta(emb.embed(guided_intent(r["intent_statement"], r["guidance_json"])),
                                 final, base)
        deltas.append({"participant_id": r["participant_id"], "round_idx": r["round_idx"],
                       "embed_fidelity": d, GUIDED_COL: d_g})
        emb.flush()   # 逐 trial 落盘:中途限流/超时不丢已付费算出的向量,重跑即续跑

    pt = pd.read_csv(CSV)
    if only_lang:
        # --lang 只重算一种语言:其他语言已算好的 Δ 原样保留(否则 `EMBED_LANG=ja` 会把 zh 的 108 行
        # 清成 NaN,stats-zh 的保真复合就悄悄退回主观三腿,zh_v1 复现不了)
        key = ["participant_id", "round_idx"]
        new = pd.DataFrame(deltas).set_index(key)
        pt = pt.set_index(key)
        for c in ("embed_fidelity", GUIDED_COL):
            if c not in pt:
                pt[c] = np.nan
            pt.loc[pt.index.intersection(new.index), c] = new[c]
        pt = pt.reset_index()
    else:
        pt = pt.drop(columns=["embed_fidelity", GUIDED_COL], errors="ignore").merge(
            pd.DataFrame(deltas), on=["participant_id", "round_idx"], how="left")
    n_ok = int(pt["embed_fidelity"].notna().sum())
    if not n_ok:  # 全 NaN 的列写进去=保真复合悄悄少一条腿,而输出看着还成功
        raise SystemExit("embed_fidelity 全为 NaN,拒绝写入空列 —— 检查 trials 的 "
                         "intent_statement/final_output 是否为空、题面是否与 topics.json 一致。")
    pt.to_csv(CSV, index=False)
    print(f"embed_fidelity 已合入 {CSV}(非空 {n_ok}/{len(pt)})")
    by_lang = (pt.groupby("lang")["embed_fidelity"].count().to_dict() if "lang" in pt else {})
    print(f"  按语言非空:{by_lang};{GUIDED_COL}(锚=意图+E 引导答案,只作敏感性、不进保真复合)"
          f"非空 {int(pt[GUIDED_COL].notna().sum())}/{len(pt)}")


def _selftest() -> None:
    rng = np.random.default_rng(0)
    intent = rng.normal(size=64)
    baseline = rng.normal(size=(20, 64))
    near = intent + 0.05 * rng.normal(size=64)   # 贴合创意
    far = rng.normal(size=64)                     # 随机
    print("cos(自身)=", round(cosine(intent, intent), 3))
    print("Δ(贴合终稿)=", round(fidelity_delta(intent, near, baseline), 3),
          " Δ(随机终稿)=", round(fidelity_delta(intent, far, baseline), 3))
    ok = fidelity_delta(intent, near, baseline) > fidelity_delta(intent, far, baseline)
    # 定义②的锚文本:C/D(无引导)= 原意图;E 只拼 chosen 非空的答案(「交给 AI」不算)
    g = json.dumps({"rounds": [{"items": [{"chosen": "雨夜"}, {"chosen": "", "ai_decided": True}]},
                               {"items": [{"chosen": " 反转 "}]}]})
    anchors_ok = (guided_intent("意图", None) == "意图"
                  and guided_intent("意图", g) == "意图\n雨夜\n反转"
                  and baseline_dir(BASELINE_LANG) == BASELINE and baseline_dir("zh") == BASELINE / "zh")
    print("锚文本/基线目录", "通过 ✅" if anchors_ok else "异常 ⚠️")
    ok = ok and anchors_ok
    print("自测", "通过 ✅(贴合稿 Δ 更高)" if ok else "异常 ⚠️")
    if not ok:
        sys.exit(1)   # 自测只 print 不设退出码 = 跑绿等于没跑


def main() -> None:
    ap = argparse.ArgumentParser(description="A6 embedding 保真 Δ")
    ap.add_argument("--selftest", action="store_true", help="纯数学自测,无 API")
    ap.add_argument("--lang", choices=("ja", "zh", "en"), default=None,
                    help="只算这一种语言的被试(其余记 NaN);缺省 = 每位被试各比自己语言的基线")
    args = ap.parse_args()
    _selftest() if args.selftest else compute(args.lang)


if __name__ == "__main__":
    main()
