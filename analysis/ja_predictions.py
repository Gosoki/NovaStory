"""日本队列的方向复核预测(2026-10-28,docs/paper/06 ㉒-10)—— 只执行 prereg 里写死的东西。

  P1–P3  prereg.JA_DIRECTION_PREDICTIONS  看过中国数据才得到的探索性发现,在日本这份留出样本上按事先写死的
                                          方向做单侧配对 t;成败都报
  B1–B3  prereg.JA_DESCRIPTIVE_CHECKS     只看方向是否与中国一致,不做检验

纳入:CSV 是 v3 的逐 trial 表(已按 v3.included_participants 过滤),只用 C / D / E 的 trial。
P1 的距离只读 embedding 缓存(data/analysis/emb_cache.json)—— 本脚本**不调 API**;缓存里缺终稿或基线的向量时,
先跑 `make embed`(要授权调 OpenAI)。`--lang zh` 可在中国数据上复现 docs/paper/17 与 14 的数字,用来核对实现。

用法:  .venv/bin/python analysis/ja_predictions.py [--lang ja] [--csv …] [--db …]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis import embed, prereg  # noqa: E402
from core.shots import strip_format  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "analysis" / "v3_per_trial.csv"
DB = ROOT / "data" / "novastory.db"
EMB_MODEL = "text-embedding-3-small"   # = embed.Embedder 的默认模型(缓存键里含模型名)


def _vec(cache: dict, text: str) -> np.ndarray | None:
    """与 embed.Embedder.embed 同一个缓存键;缓存里没有就返回 None(不调 API)。"""
    t = strip_format(text or "").strip() or "(empty)"
    v = cache.get(hashlib.sha1(f"{EMB_MODEL}\n{t}".encode()).hexdigest())
    return None if v is None else np.asarray(v, dtype=float)


def dist_base(csv_df: pd.DataFrame, db_path: Path, lang: str) -> tuple[pd.DataFrame, int]:
    """P1 的 DV:1 − cos(终稿, 同语言同题机器基线质心)。返回 (participant_id, round_idx, dist_base) 与缓存缺失数。"""
    from core import state  # noqa: PLC0415 —— 与 embed.compute 同一份题库归一化
    cache = json.loads(embed.CACHE.read_text()) if embed.CACHE.exists() else {}
    topics = state.load_topics()
    title2idx = {state.topic_text(t, "title", embed.BASELINE_LANG): i for i, t in enumerate(topics)}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tr = pd.read_sql("SELECT participant_id, round_idx, topic_json, final_output FROM trials", con)
    finally:
        con.close()
    keys = csv_df[["participant_id", "round_idx"]].drop_duplicates()
    tr = tr.merge(keys, on=["participant_id", "round_idx"])
    cents, rows, missing = {}, [], 0
    for r in tr.itertuples():
        ti = title2idx.get(state.topic_text(json.loads(r.topic_json) if r.topic_json else {}, "title",
                                            embed.BASELINE_LANG))
        if ti is None:
            continue
        if ti not in cents:
            vs = [_vec(cache, t) for t in embed._baseline_texts(ti, topics, lang)]
            missing += sum(v is None for v in vs)
            vs = [v for v in vs if v is not None]
            cents[ti] = np.mean(vs, axis=0) if vs else None
        f = _vec(cache, r.final_output)
        if f is None or cents[ti] is None:
            missing += f is None
            continue
        rows.append({"participant_id": r.participant_id, "round_idx": r.round_idx,
                     "dist_base": 1.0 - embed.cosine(f, cents[ti])})
    return pd.DataFrame(rows, columns=["participant_id", "round_idx", "dist_base"]), missing


def one_sided(df: pd.DataFrame, dv: str, hi: str, lo: str) -> dict | None:
    """hi − lo 的单侧配对 t(H1:hi > lo)。"""
    w = df.pivot_table(index="participant_id", columns="condition", values=dv, aggfunc="mean")
    if hi not in w or lo not in w:
        return None
    d = (w[hi] - w[lo]).dropna()
    if len(d) < 2 or not d.std(ddof=1) > 0:
        return {"n": len(d)}
    t = st.ttest_1samp(d, 0.0, alternative="greater")
    return {"n": len(d), "mean": float(d.mean()), "dz": float(d.mean() / d.std(ddof=1)), "p": float(t.pvalue)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lang", default="ja", choices=prereg.COHORT_LANGS,
                    help="ja = 事先写死的复核(默认);zh = 在中国数据上复现原发现,只用来核对实现")
    ap.add_argument("--csv", type=Path, default=CSV)
    ap.add_argument("--db", type=Path, default=DB)
    args = ap.parse_args(argv)

    raw = pd.read_csv(args.csv)
    df = raw[(raw["lang"] == args.lang) & raw["condition"].isin(prereg.CONFIRMATORY_CONDITIONS)]
    n = df["participant_id"].nunique()
    print("=" * 78)
    print(f"方向复核预测(prereg.JA_DIRECTION_PREDICTIONS,2026-10-28)· 队列 {args.lang} · 被试 {n} 人"
          + ("(⚠️ 中国数据:只是核对实现,不是复核)" if args.lang != "ja" else ""))
    print("=" * 78)
    if not n:
        print(f"⛔ CSV 里还没有 lang={args.lang} 的 C/D/E trial —— 无可分析。")
        return 1

    if args.db.exists():
        db_, miss = dist_base(df, args.db, args.lang)
        df = df.merge(db_, on=["participant_id", "round_idx"], how="left")
        if miss:
            print(f"  ⚠️ embedding 缓存缺 {miss} 条向量(终稿或基线)→ P1 只用到有向量的 trial;"
                  "补齐要先跑 make embed(需授权调 OpenAI)")
    alpha = prereg.ALPHA
    for p in prereg.JA_DIRECTION_PREDICTIONS:
        print(f"\n[{p['id']}] {p['dv']}:" + " 且 ".join(f"{a} > {b}" for a, b in p["contrasts"])
              + f"({p['test']})")
        print(f"     定义:{p['definition']};中国:{p['china']}")
        ok = []
        for a, b in p["contrasts"]:
            r = one_sided(df, p["dv"], a, b) if p["dv"] in df else None
            if not r or "p" not in r:
                print(f"     {a}−{b}:数据不足({r})")
                ok.append(None)
                continue
            ok.append(r["p"] < alpha)
            print(f"     {a}−{b}:n={r['n']}  差={r['mean']:+.3f}  dz={r['dz']:+.2f}  单侧 p={r['p']:.4f}"
                  f"  方向{'一致' if r['mean'] > 0 else '相反'}")
        print("     → " + ("数据不足" if None in ok else "复核成功" if all(ok) else "未复核成功(照报)"))

    print("\n[描述性方向复核](不做检验)")
    e = df[df["condition"] == "E"]
    b1 = float((e["g_custom_rate"] == 0).mean()) if len(e) else float("nan")
    d = df[df["condition"] == "D"]
    b2 = float((d["n_ai_rounds"] > 0).mean()) if len(d) else float("nan")
    legs = {"imagine": 1, "violation": -1, "not_against": 1}
    rs = [float((s * df[c]).corr(df["embed_fidelity"])) for c, s in legs.items()
          if c in df and "embed_fidelity" in df and df["embed_fidelity"].notna().sum() > 2]
    b3 = max(rs) if rs else float("nan")
    vals = {"B1": (b1, lambda v: v >= 0.5), "B2": (b2, lambda v: v < 0.5), "B3": (b3, lambda v: v < 0.3)}
    for c in prereg.JA_DESCRIPTIVE_CHECKS:
        v, rule = vals[c["id"]]
        res = "数据不足" if not np.isfinite(v) else ("与中国方向一致" if rule(v) else "与中国不一致")
        print(f"  [{c['id']}] {c['what']}:{v:.2f}(预期 {c['expect']};中国 {c['china']})→ {res}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
