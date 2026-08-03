#!/usr/bin/env python
"""A6: 论文图表(paper/10 §7.2)。无显示环境用 Agg 后端;标签用 ASCII 避开日文字体。

  fig_effort        招牌图:各条件 事前投入 vs 事后返工 堆叠条(努力再分配)
  fig_dv            主 DV 分条件:箱线 + 被试内散点连线(两个主复合各一张)
  fig_diversity     条件×题目 多样性(gzip CR,越高越同质)

输入: analysis/v3.py 的 v3_per_trial.csv(复合终点在此现算,CSV 里没有)+ 实验库
      (多样性/fig4 原料);无 CSV 则 --demo 用合成数据渲染验证。
产出: data/analysis/figures/*.png + data/analysis/fig4_coding_material.csv

用法: .venv/bin/python analysis/figures.py [--demo] [--db data/novastory.db]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import v3  # noqa: E402
from analysis.stats import build_composites  # noqa: E402

DEFAULT_DB = ROOT / "data" / "novastory.db"
CSV = ROOT / "data" / "analysis" / "v3_per_trial.csv"
FIGDIR = ROOT / "data" / "analysis" / "figures"
ORDER = ["C", "D", "E"]
COLc, COLp = "#6aa9c9", "#c98f6a"  # pre / post


def _cond_order(df):
    return [c for c in ORDER if c in df["condition"].unique()]


def fig_effort(pt: pd.DataFrame, out: Path) -> None:
    """招牌图:事前投入(引导答题)+ 事后返工 的堆叠,直观化'努力再分配'。"""
    conds = _cond_order(pt)
    pre = [pt.loc[pt.condition == c, "pre_investment"].mean() for c in conds]
    post = [pt.loc[pt.condition == c, "post_investment"].mean() for c in conds]
    fig, ax = plt.subplots(figsize=(5.2, 4))
    ax.bar(conds, pre, color=COLc, label="pre-gen investment (elicitation)")
    ax.bar(conds, post, bottom=pre, color=COLp, label="post-gen revision")
    for i, (a, b) in enumerate(zip(pre, post)):
        ax.text(i, a + b, f"{a + b:.0f}s", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("net human time (s)")
    ax.set_title("Effort reallocation across conditions")
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def fig_dv(pt: pd.DataFrame, dv: str, out: Path) -> None:
    """主 DV 分条件:箱线 + 被试内连线(配对设计标准画法)。"""
    conds = _cond_order(pt)
    wide = pt.pivot_table(index="participant_id", columns="condition", values=dv)
    fig, ax = plt.subplots(figsize=(5.2, 4))
    data = [wide[c].dropna().values for c in conds]
    ax.boxplot(data, tick_labels=conds, widths=0.5, showfliers=False)
    rng = np.random.default_rng(0)
    for _, row in wide.iterrows():
        ys = [row.get(c) for c in conds]
        xs = [i + 1 + rng.uniform(-0.06, 0.06) for i in range(len(conds))]
        if all(pd.notna(ys)):
            ax.plot(xs, ys, color="gray", alpha=0.25, lw=0.7, zorder=1)
        ax.scatter(xs, ys, s=10, color="#33526b", alpha=0.5, zorder=2)
    ax.set_ylabel(dv)
    ax.set_title(f"{dv} by condition (within-subject)")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def fig_diversity(div: pd.DataFrame, out: Path) -> None:
    conds = [c for c in ORDER if c in div["condition"].unique()]
    means = [div.loc[div.condition == c, "gzip_cr"].mean() for c in conds]
    fig, ax = plt.subplots(figsize=(4.6, 3.8))
    ax.bar(conds, means, color="#8a9a5b")
    ax.set_ylabel("gzip compression ratio (higher = more homogeneous)")
    ax.set_title("Output homogeneity by condition")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def dump_fig4_material(raw: pd.DataFrame, out: Path) -> None:
    """fig4(D 的修改请求 ↔ E 的引导维度)的编码原料。码本尚未定义(paper/8 待办),
    这里只把待编码的原文导出成 CSV 供人工/LLM 编码,不臆造类别。"""
    dims = {}
    for _, r in raw[raw["condition"] == "E"].iterrows():
        rounds = v3._loads(r.get("guidance_json"), {}).get("rounds") or []
        dims[r["participant_id"]] = "|".join(
            str(it.get("dimension", "")) for rd in rounds for it in (rd.get("items") or []))
    rows = []
    for _, r in raw[raw["condition"] == "D"].iterrows():
        for req in v3._loads(r.get("revision_requests"), []):
            rows.append({"participant_id": r["participant_id"], "round_idx": r["round_idx"],
                         "topic": v3._topic_title(r.get("topic_json")),
                         "revision_request": req.get("text", ""),
                         "e_guidance_dimensions": dims.get(r["participant_id"], "")})
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"fig4 编码原料(D 修改请求 × E 引导维度){len(rows)} 条 → {out}")


def _demo_df() -> pd.DataFrame:
    """合成 per-trial:E 事前投入高、事后返工少;所有权 E>D>C(仅供渲染验证)。"""
    rng = np.random.default_rng(3)
    rows = []
    for s in range(36):
        base = rng.normal(0, 0.6)
        rows += [
            {"participant_id": s, "condition": "C", "pre_investment": 0,
             "post_investment": max(0, rng.normal(15, 6)),
             "ownership_composite": 3.2 + base + rng.normal(0, .5)},
            {"participant_id": s, "condition": "D", "pre_investment": 0,
             "post_investment": max(0, rng.normal(70, 20)),
             "ownership_composite": 4.3 + base + rng.normal(0, .5)},
            {"participant_id": s, "condition": "E", "pre_investment": max(0, rng.normal(45, 12)),
             "post_investment": max(0, rng.normal(30, 12)),
             "ownership_composite": 5.1 + base + rng.normal(0, .5)},
        ]
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="A6 图表")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()
    FIGDIR.mkdir(parents=True, exist_ok=True)

    if args.demo or not CSV.exists():
        if not CSV.exists() and not args.demo:
            print(f"(未找到 {CSV},用合成数据渲染验证)")
        pt = _demo_df()
    else:
        # 复合终点只在 stats.py 内存里构建、从不写回 CSV,故这里自己算(否则主 DV 图永远出不来)
        pt = build_composites(pd.read_csv(CSV))

    fig_effort(pt, FIGDIR / "fig_effort.png")
    for dv, name in (("ownership_composite", "fig_ownership.png"),
                     ("fidelity_composite", "fig_fidelity.png")):
        if dv in pt and pt[dv].notna().any():
            fig_dv(pt, dv, FIGDIR / name)
        else:
            print(f"(缺 {dv},跳过该图)")

    if not args.demo and args.db.exists():  # 多样性/fig4 原料要回到库里取成稿原文
        raw = v3.load(args.db)
        div = v3.diversity_by_group(raw)
        if len(div):
            fig_diversity(div, FIGDIR / "fig_diversity.png")
        else:
            print("(每组 <2 稿,跳过多样性图)")
        dump_fig4_material(raw, CSV.parent / "fig4_coding_material.csv")
    print("图已写入", FIGDIR, "→", ", ".join(p.name for p in sorted(FIGDIR.glob("*.png"))))


if __name__ == "__main__":
    main()
