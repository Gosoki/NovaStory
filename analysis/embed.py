#!/usr/bin/env python
"""A6: embedding 相对基线的意图保真 Δ(paper/14 §5 保真主力)。

Δ = cos(创意, 终稿) − cos(创意, 同题机器基线质心)
   把'纯 AI 本来就有多贴'当零点,只主张人类介入带来的增量,避免绝对余弦陷阱
   (Steck 2024:cos 不等于语义相似)。多模型稳健性建议 ≥3 个,此处先接 OpenAI
   text-embedding-3,本地 e5(需 torch)留作可选副模型。

需要:OpenAI key(secrets 里的 openai.com 配置)+ data/baseline/(先跑 make baseline)。
产出:把 embed_fidelity 列并入 data/analysis/v3_per_trial.csv,供 stats.py 进保真复合。

用法: .venv/bin/python analysis/embed.py            # 计算并合入
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

from core.shots import strip_format  # noqa: E402

DATA = ROOT / "data"
BASELINE = DATA / "baseline"
CSV = DATA / "analysis" / "v3_per_trial.csv"
CACHE = DATA / "analysis" / "emb_cache.json"
SECRETS = ROOT / ".streamlit" / "secrets.toml"
_MIN_BASELINE = 5  # 质心的机器稿下限:更少则「纯 AI 本来有多贴」这个零点全是抽样噪声


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
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(self.cache))


def _baseline_texts(topic_idx: int, topics: list) -> list[str]:
    """读一题的机器基线,并校验这份基线确实属于这道题。

    topic{i}.jsonl 只按 topics.json 的顺序命名:题目一旦重排/改写,序号就会把基线
    安到别的题上(错配是静默的)。baseline_gen 每行记了 topic_idx,缺省又把该题
    scenario 当 seed,故用 seed 反查题目身份;基线缺失/太少一律硬失败,不返回 NaN。"""
    p = BASELINE / f"topic{topic_idx}.jsonl"
    if not p.exists():
        raise SystemExit(f"缺少机器基线 {p} —— 先跑: make baseline")
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(recs) < _MIN_BASELINE:
        raise SystemExit(f"{p} 只有 {len(recs)} 份基线(<{_MIN_BASELINE}),质心不可靠 —— "
                         "重跑: make baseline")
    bad = {r.get("topic_idx") for r in recs} - {topic_idx}
    if bad:
        raise SystemExit(f"{p} 内记录的 topic_idx={bad} 与文件名不符 —— 基线与题目错配,"
                         "删掉 data/baseline/ 重跑 make baseline")
    from core import prompts as _prompts  # noqa: PLC0415 — avoid a UI import at module load
    scen = [_prompts.scenario_text(t, "ja", with_note=False) for t in topics]
    seed = (recs[0].get("seed") or "").strip()
    if seed and seed != scen[topic_idx]:
        if seed in scen:
            raise SystemExit(f"{p} 是用第 {scen.index(seed)} 题的情境生成的 —— topics.json 已被"
                             "重排/改写,基线与题目错配,删掉 data/baseline/ 重跑 make baseline")
        print(f"⚠️ {p.name} 的 seed 不是任何题的情境(可能用了 --seeds-file):题目身份只能按"
              "文件序号信任,请确认 topics.json 自生成基线以来未改动。")
    return [r["text"] for r in recs]


def _check_baseline_model(topic_idxs, trial_models: set[str]) -> None:
    """基线的生成模型必须与被试实际用的生成模型一致。

    Δ = sim(创意,终稿) − sim(创意,**基线**质心),零点就是「纯 AI 会写成什么样」。基线若
    是另一个模型(或另一次快照)写的,这个零点量的就不是同一件事,Δ 失去意义——而且**事后
    补不回来**:正式数据一旦采完,当时的模型可能已经下线。baseline_gen 每行记了 model,
    trials 每行也记了 model,直接对上。不一致 → 硬失败,不产出看着能用的假 Δ。"""
    base_models = set()
    for i in topic_idxs:
        pth = BASELINE / f"topic{i}.jsonl"
        first = next((l for l in pth.read_text(encoding="utf-8").splitlines() if l.strip()), "")
        if first:
            base_models.add(json.loads(first).get("model") or "?")
    if len(base_models) > 1:
        raise SystemExit(f"data/baseline/ 里混了多个模型 {sorted(base_models)} —— "
                         "同一批基线必须同模型,删掉 data/baseline/ 重跑 make baseline")
    if base_models and trial_models and base_models != trial_models:
        raise SystemExit(
            f"基线模型 {sorted(base_models)} 与被试实际用的生成模型 {sorted(trial_models)} 不一致。\n"
            "   Δ 的零点(「纯 AI 本来就有多贴」)必须由**同一个模型**产生,否则 Δ 量的不是同一件事。\n"
            "   处置:用正式采数的那个模型快照重跑 `make baseline`(改 secrets 的 api_configs[0] "
            "或 --config-index),再跑 make embed。")


def compute() -> None:
    import pandas as pd
    if not CSV.exists():
        raise SystemExit("先跑 analysis/v3.py 生成 v3_per_trial.csv。")
    if not BASELINE.exists():
        raise SystemExit(f"缺少机器基线目录 {BASELINE} —— Δ 的零点就是它,先跑: make baseline")
    con = sqlite3.connect(f"file:{DATA/'novastory.db'}?mode=ro", uri=True)
    trials = pd.read_sql("SELECT participant_id, round_idx, intent_statement, final_output, "
                         "topic_json, model FROM trials", con)
    con.close()

    # topic title → baseline index(按 topics.json 顺序,由 _baseline_texts 复核身份)
    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    title2idx = {t["title"]["ja"]: i for i, t in enumerate(topics)}
    trials["topic_idx"] = [
        title2idx.get(((json.loads(tj) if tj else {}).get("title") or {}).get("ja"))
        for tj in trials["topic_json"]]
    used = sorted({int(i) for i in trials["topic_idx"].dropna().unique()})
    if not used:
        raise SystemExit("没有一条 trial 的题目能对上 topics.json(题面被改过?)——无法配基线。")
    # 先纯文件校验+读齐所有用到的基线,再花任何 API 调用
    _check_baseline_model(used, {m for m in trials["model"].dropna().unique() if str(m).strip()})
    base_texts = {i: _baseline_texts(i, topics) for i in used}

    emb = Embedder()
    base_vecs = {i: np.array([emb.embed(t) for t in ts]) for i, ts in base_texts.items()}
    deltas = []
    for _, r in trials.iterrows():
        ti = r["topic_idx"]
        if pd.isna(ti) or not r["intent_statement"] or not r["final_output"]:
            d = np.nan
        else:
            d = fidelity_delta(emb.embed(r["intent_statement"]),
                               emb.embed(r["final_output"]), base_vecs[int(ti)])
        deltas.append({"participant_id": r["participant_id"], "round_idx": r["round_idx"],
                       "embed_fidelity": d})
    emb.flush()

    pt = pd.read_csv(CSV)
    pt = pt.drop(columns=["embed_fidelity"], errors="ignore").merge(
        pd.DataFrame(deltas), on=["participant_id", "round_idx"], how="left")
    n_ok = int(pt["embed_fidelity"].notna().sum())
    if not n_ok:  # 全 NaN 的列写进去=保真复合悄悄少一条腿,而输出看着还成功
        raise SystemExit("embed_fidelity 全为 NaN,拒绝写入空列 —— 检查 trials 的 "
                         "intent_statement/final_output 是否为空、题面是否与 topics.json 一致。")
    pt.to_csv(CSV, index=False)
    print(f"embed_fidelity 已合入 {CSV}(非空 {n_ok}/{len(pt)})")


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
    print("自测", "通过 ✅(贴合稿 Δ 更高)" if ok else "异常 ⚠️")


def main() -> None:
    ap = argparse.ArgumentParser(description="A6 embedding 保真 Δ")
    ap.add_argument("--selftest", action="store_true", help="纯数学自测,无 API")
    args = ap.parse_args()
    _selftest() if args.selftest else compute()


if __name__ == "__main__":
    main()
