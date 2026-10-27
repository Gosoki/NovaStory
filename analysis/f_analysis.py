"""第 4 条件 F 的日本队列内部分析(2026-10-27,docs/paper/06 ㉖)—— 只执行 prereg 里写死的东西。

  主要检验  prereg.F_PRIMARY    逐镜头「这是我的想法」比例 mine_ratio,F vs E,双侧配对 t(+ Wilcoxon、95% CI)
  次要      prereg.F_SECONDARY  带事先写明的方向,只作描述,不进确证判定
  操纵检查  prereg.F_MANIPULATION_CHECK  F 里全部回答的总字数与题数分布(3-5 题;从库里的 guidance_json 读)

纳入:CSV 是 v3 的逐 trial 表(已按 v3.included_participants 过滤:ja 要交满 4 份问卷);
这里再只取 lang ∈ 4 条件设计的语言、且 F 与对比条件都有的被试。
次要里的 fidelity_composite 用 stats.build_composites 在**日本 4 个条件的全部 trial** 上算 z
(与中国 / 合并分析的复合不在同一把尺上 —— 那两者不含 F,见 stats.drop_non_confirmatory)。

用法:  .venv/bin/python analysis/f_analysis.py [--csv data/analysis/v3_per_trial.csv] [--db data/novastory.db]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis import prereg  # noqa: E402
from core import config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "analysis" / "v3_per_trial.csv"
DB = ROOT / "data" / "novastory.db"


def paired(df: pd.DataFrame, dv: str, hi: str, lo: str) -> dict | None:
    """每位被试一个 hi − lo 差值:配对 t(双侧)、Wilcoxon、均值差的 95% CI、dz。"""
    w = df.pivot_table(index="participant_id", columns="condition", values=dv, aggfunc="mean")
    if hi not in w or lo not in w:
        return None
    d = (w[hi] - w[lo]).dropna()
    n = len(d)
    if n < 2:
        return {"n": n}
    t = st.ttest_1samp(d, 0.0)
    se = d.std(ddof=1) / np.sqrt(n)
    half = st.t.ppf(0.975, n - 1) * se
    try:
        p_w = float(st.wilcoxon(d).pvalue) if (d != 0).any() else float("nan")
    except ValueError:
        p_w = float("nan")
    sd = d.std(ddof=1)
    return {"n": n, "mean": float(d.mean()), "ci_lo": float(d.mean() - half), "ci_hi": float(d.mean() + half),
            "dz": float(d.mean() / sd) if sd > 1e-9 else float("nan"), "p_t": float(t.pvalue), "p_wilcoxon": p_w}


def _fmt(r: dict | None) -> str:
    if not r:
        return "数据不足(缺这两个条件之一)"
    if "mean" not in r:
        return f"n={r['n']}(不足 2 人,不检验)"
    return (f"n={r['n']}  差={r['mean']:+.3f}  95%CI[{r['ci_lo']:+.3f}, {r['ci_hi']:+.3f}]  "
            f"dz={r['dz']:+.2f}  配对t p={r['p_t']:.4f}  Wilcoxon p={r['p_wilcoxon']:.4f}")


def manipulation_check(db_path: Path, ids: set[int]) -> str:
    """F 里全部回答的总字数 + 题数分布(3-5 题)+ 退回固定题面的题数(题面由 AI 调整失败时)。"""
    if not db_path.exists():
        return f"(没有 {db_path},跳过)"
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT participant_id, guidance_json FROM trials WHERE condition='F'").fetchall()
    finally:
        con.close()
    chars, n_q, n_fb, n_items = [], [], 0, 0
    for pid, g in rows:
        if int(pid) not in ids or not g:
            continue
        for rnd in json.loads(g).get("rounds", []):
            if rnd.get("source") != "interview":
                continue
            items = rnd.get("items", [])
            chars.append(sum(len(str(it.get("chosen") or "")) for it in items))
            n_q.append(len(items))
            n_fb += sum(bool(it.get("fallback")) for it in items)
            n_items += len(items)
    if not chars:
        return "(库里没有可用的 F 采访记录)"
    c = pd.Series(chars)
    dist = pd.Series(n_q).value_counts().sort_index().to_dict()
    return (f"n={len(c)}  回答总字数 中位 {c.median():.0f}(IQR {c.quantile(.25):.0f}–{c.quantile(.75):.0f},"
            f"最少 {c.min()}、最多 {c.max()});题数分布 {dist};退回固定题面 {n_fb}/{n_items} 题")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", type=Path, default=CSV)
    ap.add_argument("--db", type=Path, default=DB)
    args = ap.parse_args(argv)

    raw = pd.read_csv(args.csv)
    langs = sorted(config.FOUR_COND_LANGS)
    df = raw[raw["lang"].isin(langs)] if "lang" in raw else raw.iloc[0:0]
    f = prereg.F_CONDITION
    have_f = set(df.loc[df["condition"] == f, "participant_id"]) if len(df) else set()
    df = df[df["participant_id"].isin(have_f)]
    print("=" * 78)
    print(f"第 4 条件 F 的日本内部分析(prereg.F_PRIMARY,2026-10-27)· 队列 {langs} · 被试 {len(have_f)} 人")
    print(f"  主要检验:{prereg.F_PRIMARY}")
    print("=" * 78)
    if not have_f:
        print("⛔ CSV 里还没有 F 的 trial(日本数据还没进库?)—— 无可分析。")
        return 1

    hi, lo = prereg.F_PRIMARY["contrast"]
    print(f"\n[主要] {prereg.F_PRIMARY['dv']}  {hi} − {lo}(双侧,α={prereg.F_PRIMARY['alpha']})")
    prim = paired(df, prereg.F_PRIMARY["dv"], hi, lo)
    print("  " + _fmt(prim))
    if prim and "p_t" in prim:
        sig = prim["p_t"] < prereg.F_PRIMARY["alpha"]
        print("  → " + (f"差异显著(方向:{'F 高于 E' if prim['mean'] > 0 else 'E 高于 F'})" if sig
                        else "未检出差异(不等于两者相同;看 95% CI 能排除多大的差)"))

    try:
        from analysis.stats import build_composites  # noqa: PLC0415
        comp = build_composites(df)
    except Exception as e:  # noqa: BLE001 —— 复合建不出来只影响一条次要
        print(f"  注:fidelity_composite 建不出来({type(e).__name__}: {e}),该次要项跳过")
        comp = df
    print("\n[次要](只作描述;方向是事先写明的预测)")
    for name, direction in prereg.F_SECONDARY:
        dv, _, pair = name.partition(" · ")
        a, b = (pair.split("−") if pair else (hi, lo))
        src = comp if dv in comp.columns else df
        r = paired(src, dv, a, b) if dv in src.columns else None
        print(f"  {dv:<20} {a}−{b}  预测 {direction:<6}  " + _fmt(r))

    print(f"\n[操纵检查] {prereg.F_MANIPULATION_CHECK}")
    print("  " + manipulation_check(args.db, have_f))
    return 0


if __name__ == "__main__":
    sys.exit(main())
