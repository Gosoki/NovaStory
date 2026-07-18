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


def _baseline_vecs(emb: Embedder, topic_idx: int) -> np.ndarray | None:
    p = BASELINE / f"topic{topic_idx}.jsonl"
    if not p.exists():
        return None
    texts = [json.loads(l)["text"] for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return np.array([emb.embed(t) for t in texts]) if texts else None


def compute() -> None:
    import pandas as pd
    if not CSV.exists():
        raise SystemExit("先跑 analysis/v3.py 生成 v3_per_trial.csv。")
    emb = Embedder()
    con = sqlite3.connect(f"file:{DATA/'novastory.db'}?mode=ro", uri=True)
    trials = pd.read_sql("SELECT participant_id, round_idx, intent_statement, final_output, "
                         "topic_json FROM trials", con)
    con.close()

    # topic title → baseline index(按 topics.json 顺序)
    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    title2idx = {t["title"]["ja"]: i for i, t in enumerate(topics)}
    base_cache: dict[int, np.ndarray] = {}

    deltas = []
    for _, r in trials.iterrows():
        tj = json.loads(r["topic_json"]) if r["topic_json"] else {}
        ti = title2idx.get((tj.get("title") or {}).get("ja"))
        if ti is None or ti not in base_cache:
            if ti is not None:
                bv = _baseline_vecs(emb, ti)
                if bv is not None:
                    base_cache[ti] = bv
        bv = base_cache.get(ti)
        if bv is None or not r["intent_statement"] or not r["final_output"]:
            d = np.nan
        else:
            d = fidelity_delta(emb.embed(r["intent_statement"]),
                               emb.embed(r["final_output"]), bv)
        deltas.append({"participant_id": r["participant_id"], "round_idx": r["round_idx"],
                       "embed_fidelity": d})
    emb.flush()

    pt = pd.read_csv(CSV)
    pt = pt.drop(columns=["embed_fidelity"], errors="ignore").merge(
        pd.DataFrame(deltas), on=["participant_id", "round_idx"], how="left")
    pt.to_csv(CSV, index=False)
    print(f"embed_fidelity 已合入 {CSV}(非空 {pt['embed_fidelity'].notna().sum()}/{len(pt)})")


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
