# NOTE (2026-06-13): HLZ / MC1 / MC2 metrics below are DEPRECATED (paper/7 D10);
# diversity/baseline parts remain in use for exploratory analysis (paper/8 A3).
#!/usr/bin/env python
"""T7.4 指标计算 — HLZ / 组内多样性 / 采样地板 / 编辑距离 / 意图保真 / MC1 / MC2。

库 + CLI。所有文本先经 core.shots.strip_format 剥离格式再 embedding;
embedding 结果缓存于 data/analysis/emb_cache.json(键 = sha1(backend + text))。

输出:
  data/analysis/metrics.csv            tidy 长表 (level, metric, value, …)
  data/analysis/metrics_per_trial.csv  per-trial 宽表(stats.py 的输入)
  data/analysis/pairwise_sims.json     格内成稿两两余弦(置换检验的输入)
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.shots import strip_format  # noqa: E402

DATA = ROOT / "data"
ANALYSIS_DIR = DATA / "analysis"
CACHE_PATH = ANALYSIS_DIR / "emb_cache.json"
SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"

# ---------------- pure math(与 backend 无关,可单测)----------------


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def cosine_dist(a: np.ndarray, b: np.ndarray) -> float:
    return 1.0 - cosine_sim(a, b)


def hlz(output_vec: np.ndarray, ghost_vecs: np.ndarray) -> dict:
    """HLZ: z = (d(产出, ghost 质心) − ghost 内到质心距离均值) / ghost 内标准差。

    距离用 cosine;返回 {z, d_out, d_mean, d_sd, k}。ghost 退化(<2 份或
    零方差)时 z 为 nan。"""
    ghost_vecs = np.asarray(ghost_vecs, dtype=float)
    centroid = ghost_vecs.mean(axis=0)
    d_out = cosine_dist(np.asarray(output_vec, dtype=float), centroid)
    d_in = np.array([cosine_dist(g, centroid) for g in ghost_vecs])
    d_mean = float(d_in.mean())
    d_sd = float(d_in.std(ddof=1)) if len(d_in) > 1 else float("nan")
    z = (d_out - d_mean) / d_sd if d_sd and np.isfinite(d_sd) and d_sd > 0 else float("nan")
    return {"z": z, "d_out": d_out, "d_mean": d_mean, "d_sd": d_sd, "k": len(d_in)}


def pairwise_sim_matrix(vecs: np.ndarray) -> np.ndarray:
    """n×n 余弦相似度矩阵(向量先归一化)。"""
    vecs = np.asarray(vecs, dtype=float)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = vecs / norms
    return unit @ unit.T


def group_diversity(vecs: np.ndarray) -> float:
    """组内多样性 = 1 − 平均两两余弦相似度;n<2 时 nan。"""
    n = len(vecs)
    if n < 2:
        return float("nan")
    sims = pairwise_sim_matrix(vecs)
    iu = np.triu_indices(n, k=1)
    return float(1.0 - sims[iu].mean())


def edit_distance(a: str, b: str) -> float:
    """归一化编辑距离 = 1 − difflib.SequenceMatcher.ratio()(字符级,0-1)。"""
    a, b = (a or "").strip(), (b or "").strip()
    if not a and not b:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, a, b).ratio()


# ---------------- embedding backends ----------------


class OpenAIBackend:
    """text-embedding-3-large;按 base_url 在 secrets.toml 里定位 OpenAI 官方配置
    (配置顺序可随意调整,不再依赖固定序号)。"""

    name = "openai"
    model = "text-embedding-3-large"

    def __init__(self) -> None:
        import tomllib

        from openai import OpenAI

        with SECRETS_PATH.open("rb") as f:
            configs = tomllib.load(f).get("api_configs", [])
        c = next(
            (x for x in configs if "api.openai.com" in (x.get("base_url") or "")),
            None,
        )
        if c is None:
            raise RuntimeError("secrets.toml 中没有 base_url 为 api.openai.com 的配置(embedding 用)")
        if not (c.get("api_key") or "").strip():
            raise RuntimeError("secrets.toml 的 OpenAI 配置 api_key 为空")
        self._client = OpenAI(api_key=c["api_key"], base_url=c.get("base_url") or None)

    def embed(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), 100):  # API 批量上限内分块
            resp = self._client.embeddings.create(
                model=self.model, input=texts[i : i + 100]
            )
            out.extend(d.embedding for d in resp.data)
        return np.asarray(out, dtype=float)


class LocalBackend:
    """sentence-transformers / intfloat/multilingual-e5-small(惰性 import)。"""

    name = "local"
    model_name = "intfloat/multilingual-e5-small"

    def __init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "本地 embedding 后端需要 sentence-transformers:\n"
                "  .venv/bin/pip install sentence-transformers\n"
                "(见 analysis/requirements-analysis.txt 的可选依赖说明)"
            ) from e
        self._model = SentenceTransformer(self.model_name)

    def embed(self, texts: list[str]) -> np.ndarray:
        # e5 系列要求加前缀;对称相似度任务两侧统一用 "query: "
        return np.asarray(
            self._model.encode(
                [f"query: {t}" for t in texts], normalize_embeddings=True
            ),
            dtype=float,
        )


def make_backend(name: str):
    if name == "openai":
        return OpenAIBackend()
    if name == "local":
        return LocalBackend()
    raise ValueError(f"unknown backend: {name}")


# ---------------- embedding cache ----------------


def _cache_key(text: str, backend: str) -> str:
    return hashlib.sha1((backend + "\x00" + text).encode("utf-8")).hexdigest()


class Embedder:
    """带 JSON 文件缓存的批量 embedding;只对未命中文本调用后端。"""

    def __init__(self, backend) -> None:
        self.backend = backend
        self._cache: dict[str, list[float]] = {}
        if CACHE_PATH.exists():
            self._cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))

    def embed_all(self, texts: list[str]) -> dict[str, np.ndarray]:
        """返回 {text: vec};texts 去重后查缓存,未命中的批量请求。"""
        uniq = list(dict.fromkeys(t for t in texts if (t or "").strip()))
        missing = [t for t in uniq if _cache_key(t, self.backend.name) not in self._cache]
        if missing:
            print(f"embedding: {len(missing)}/{len(uniq)} 条未命中缓存,请求后端 …")
            vecs = self.backend.embed(missing)
            for t, v in zip(missing, vecs):
                self._cache[_cache_key(t, self.backend.name)] = [float(x) for x in v]
            self._save()
        return {
            t: np.asarray(self._cache[_cache_key(t, self.backend.name)]) for t in uniq
        }

    def _save(self) -> None:
        ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(self._cache), encoding="utf-8")


# ---------------- data loading ----------------


def load_trials_df(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM trials ORDER BY id", conn)
    conn.close()
    return df


def topic_index(topic_json: str, topics: list[dict]) -> int:
    """按 title 匹配 data/topics.json 的下标;未匹配返回 -1。"""
    try:
        title = json.loads(topic_json or "{}").get("title", "")
    except json.JSONDecodeError:
        return -1
    for i, t in enumerate(topics):
        if t.get("title") == title:
            return i
    return -1


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def load_baseline() -> dict[int, list[dict]]:
    """{topic_idx: [records]},无文件时为空 dict。"""
    out: dict[int, list[dict]] = {}
    for path in sorted((DATA / "baseline").glob("topic*.jsonl")):
        recs = load_jsonl(path)
        if recs:
            out[int(recs[0]["topic_idx"])] = recs
    return out


# ---------------- main computation ----------------


def run(backend_name: str, db_path: Path) -> None:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    trials = load_trials_df(db_path)
    baseline = load_baseline()
    if trials.empty and not baseline:
        sys.exit("既无 trials 数据也无 baseline 数据 — 先跑实验或 scripts/baseline_gen.py")

    ghosts: dict[int, list[dict]] = {}
    if not trials.empty:
        trials["topic_idx"] = trials["topic_json"].map(lambda s: topic_index(s, topics))
        for tid in trials["id"]:
            recs = load_jsonl(DATA / "ghosts" / f"trial{tid}.jsonl")
            if recs:
                ghosts[int(tid)] = recs

    # ---- 收集全部待 embedding 文本(成稿/ghost/baseline 先剥格式) ----
    texts: list[str] = []
    stripped: dict[str, str] = {}  # 原文 -> 剥离后文本

    def add_stripped(raw: str) -> str:
        s = strip_format(raw or "")
        stripped[raw or ""] = s
        if s:
            texts.append(s)
        return s

    for _, tr in trials.iterrows():
        add_stripped(tr.get("final_output") or "")
        if (tr.get("intent_statement") or "").strip():
            texts.append(tr["intent_statement"].strip())
        if tr["condition"] == "E" and tr.get("dissent_json"):
            dj = json.loads(tr["dissent_json"])
            for t in [dj.get("dissent", "")] + list(dj.get("defaults", [])):
                if (t or "").strip():
                    texts.append(t.strip())
            if (tr.get("edited_outline") or "").strip():
                texts.append(tr["edited_outline"].strip())
    for recs in ghosts.values():
        for r in recs:
            add_stripped(r["text"])
    for recs in baseline.values():
        for r in recs:
            add_stripped(r["text"])

    embedder = Embedder(make_backend(backend_name))
    emb = embedder.embed_all(texts)

    def vec(text: str) -> np.ndarray | None:
        return emb.get((text or "").strip() or None)

    def vec_stripped(raw: str) -> np.ndarray | None:
        return emb.get(stripped.get(raw or "", ""))

    tidy: list[dict] = []
    per_trial: list[dict] = []

    def add(metric: str, value: float, **keys) -> None:
        tidy.append({"metric": metric, "value": value, "backend": backend_name, **keys})

    # ---- per-trial: HLZ / 意图-成稿余弦 / 编辑距离 / MC1 ----
    for _, tr in trials.iterrows():
        tid, cond = int(tr["id"]), tr["condition"]
        keys = {
            "level": "trial", "trial_id": tid,
            "participant_id": int(tr["participant_id"]),
            "condition": cond, "topic_idx": int(tr["topic_idx"]),
        }
        row: dict = {
            "trial_id": tid, "participant_id": int(tr["participant_id"]),
            "round_idx": tr.get("round_idx"), "condition": cond,
            "topic_idx": int(tr["topic_idx"]),
            "adjudication": tr.get("adjudication"),
            "t_total": tr.get("t_total"), "t_llm_wait": tr.get("t_llm_wait"),
        }
        try:
            row["t_net"] = float(tr["t_total"]) - float(tr["t_llm_wait"])
        except (TypeError, ValueError):
            row["t_net"] = float("nan")

        fv = vec_stripped(tr.get("final_output") or "")
        gh = ghosts.get(tid, [])
        gvecs = [vec_stripped(g["text"]) for g in gh]
        gvecs = [v for v in gvecs if v is not None]
        if fv is not None and len(gvecs) >= 2:
            h = hlz(fv, np.array(gvecs))
            row.update(hlz_z=h["z"], hlz_d_out=h["d_out"], hlz_k=h["k"])
            add("hlz_z", h["z"], **keys)
        else:
            row.update(hlz_z=float("nan"), hlz_d_out=float("nan"), hlz_k=len(gvecs))

        iv = vec(tr.get("intent_statement") or "")
        if fv is not None and iv is not None:
            row["intent_cos"] = cosine_sim(iv, fv)
            add("intent_cos", row["intent_cos"], **keys)
        else:
            row["intent_cos"] = float("nan")

        if cond in ("D", "E"):
            row["edit_dist"] = edit_distance(
                tr.get("ai_outline") or "", tr.get("edited_outline") or ""
            )
            add("edit_dist", row["edit_dist"], **keys)
        else:
            row["edit_dist"] = float("nan")

        # MC1(仅 E):异议距默认质心 vs 用户大纲距默认质心
        row.update(mc1_d_dissent=float("nan"), mc1_d_user=float("nan"), mc1_pass=None)
        if cond == "E" and tr.get("dissent_json"):
            dj = json.loads(tr["dissent_json"])
            dvecs = [vec(t) for t in dj.get("defaults", [])]
            dvecs = [v for v in dvecs if v is not None]
            dis_v = vec(dj.get("dissent", ""))
            usr_v = vec(tr.get("edited_outline") or "")
            if dvecs and dis_v is not None and usr_v is not None:
                centroid = np.mean(dvecs, axis=0)
                d_dis = cosine_dist(dis_v, centroid)
                d_usr = cosine_dist(usr_v, centroid)
                row.update(
                    mc1_d_dissent=d_dis, mc1_d_user=d_usr,
                    mc1_pass=int(d_dis > d_usr),
                )
                add("mc1_d_dissent", d_dis, **keys)
                add("mc1_d_user", d_usr, **keys)
        per_trial.append(row)

    # ---- 格(条件×题目)内多样性 + 置换检验输入 ----
    sims_export: dict[str, dict] = {}
    if not trials.empty:
        for topic_idx, sub in trials.groupby("topic_idx"):
            fvecs, conds, tids, pids = [], [], [], []
            for _, tr in sub.iterrows():
                v = vec_stripped(tr.get("final_output") or "")
                if v is not None:
                    fvecs.append(v)
                    conds.append(tr["condition"])
                    tids.append(int(tr["id"]))
                    pids.append(int(tr["participant_id"]))
            if len(fvecs) >= 2:
                sims_export[str(int(topic_idx))] = {
                    "trial_ids": tids, "conditions": conds, "participants": pids,
                    "sims": pairwise_sim_matrix(np.array(fvecs)).tolist(),
                }
            for cond in ("C", "D", "E"):
                cell = [v for v, c in zip(fvecs, conds) if c == cond]
                if len(cell) >= 2:
                    add(
                        "cell_diversity", group_diversity(np.array(cell)),
                        level="cell", condition=cond, topic_idx=int(topic_idx),
                        n=len(cell),
                    )

    # ---- 采样地板:baseline 同款多样性 ----
    base_sims_by_topic: dict[int, list[float]] = {}
    for topic_idx, recs in baseline.items():
        bvecs = [vec_stripped(r["text"]) for r in recs]
        bvecs = [v for v in bvecs if v is not None]
        if len(bvecs) >= 2:
            add(
                "baseline_diversity", group_diversity(np.array(bvecs)),
                level="topic", topic_idx=topic_idx, n=len(bvecs),
            )
            m = pairwise_sim_matrix(np.array(bvecs))
            iu = np.triu_indices(len(bvecs), k=1)
            base_sims_by_topic[topic_idx] = m[iu].tolist()

    # ---- MC2:异议间两两相似度 vs 机器基线相似度 ----
    if not trials.empty:
        e_rows = trials[(trials["condition"] == "E") & trials["dissent_json"].notna()]
        dis_by_topic: dict[int, list[np.ndarray]] = {}
        for _, tr in e_rows.iterrows():
            v = vec(json.loads(tr["dissent_json"]).get("dissent", ""))
            if v is not None:
                dis_by_topic.setdefault(int(tr["topic_idx"]), []).append(v)
        for topic_idx, dvecs in dis_by_topic.items():
            if len(dvecs) < 2:
                continue
            m = pairwise_sim_matrix(np.array(dvecs))
            iu = np.triu_indices(len(dvecs), k=1)
            mean_dis = float(m[iu].mean())
            add("mc2_dissent_sim", mean_dis, level="topic", topic_idx=topic_idx, n=len(dvecs))
            base = base_sims_by_topic.get(topic_idx)
            if base:
                add("mc2_baseline_sim", float(np.mean(base)), level="topic", topic_idx=topic_idx)
                add("mc2_pass", int(mean_dis <= float(np.mean(base))), level="topic", topic_idx=topic_idx)

    # ---- 输出 ----
    ts = datetime.now().isoformat(timespec="seconds")
    tidy_df = pd.DataFrame(tidy)
    tidy_df["ts"] = ts
    tidy_df.to_csv(ANALYSIS_DIR / "metrics.csv", index=False)
    pd.DataFrame(per_trial).to_csv(ANALYSIS_DIR / "metrics_per_trial.csv", index=False)
    (ANALYSIS_DIR / "pairwise_sims.json").write_text(
        json.dumps(sims_export, ensure_ascii=False), encoding="utf-8"
    )
    print(f"tidy 指标 {len(tidy_df)} 行 → {ANALYSIS_DIR / 'metrics.csv'}")
    print(f"per-trial {len(per_trial)} 行 → {ANALYSIS_DIR / 'metrics_per_trial.csv'}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="计算 HLZ / 多样性 / 编辑距离 / 意图保真 / MC1 / MC2 → tidy CSV"
    )
    ap.add_argument(
        "--backend", choices=("openai", "local"), default="openai",
        help="embedding 后端:openai=text-embedding-3-large(secrets 中 base_url 为 api.openai.com 的配置);"
             "local=multilingual-e5-small(需 sentence-transformers)",
    )
    ap.add_argument("--db", type=Path, default=DATA / "novastory.db")
    args = ap.parse_args()
    run(args.backend, args.db)


if __name__ == "__main__":
    main()
