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

from analysis import prereg, v3  # noqa: E402
from analysis.stats import build_composites  # noqa: E402

DEFAULT_DB = ROOT / "data" / "novastory.db"
CSV = ROOT / "data" / "analysis" / "v3_per_trial.csv"
# 与 analysis.stats 同源的默认人群(2026-09-07 拍板 = 全样本)。
DEFAULT_POPULATION = "all"

FIGDIR = ROOT / "data" / "analysis" / "figures"
ORDER = ["C", "D", "E"]
COL_PRE, COL_POST = "#6aa9c9", "#c98f6a"   # 事前投入 / 事后返工(别叫 COLc:会被读成 C 条件)


def _cond_order(df):
    return [c for c in ORDER if c in df["condition"].unique()]


def fig_effort(pt: pd.DataFrame, out: Path, population: str = "") -> None:
    """招牌图:事前投入(引导答题)+ 事后返工 的堆叠,直观化'努力再分配'。均值 ± SEM。"""
    conds = _cond_order(pt)
    n_by = [int(pt.loc[pt.condition == c, "post_investment"].notna().sum()) for c in conds]
    sem = [float(pt.loc[pt.condition == c, ["pre_investment", "post_investment"]].fillna(0).sum(axis=1).sem()) for c in conds]
    pre = [pt.loc[pt.condition == c, "pre_investment"].mean() for c in conds]
    post = [pt.loc[pt.condition == c, "post_investment"].mean() for c in conds]
    fig, ax = plt.subplots(figsize=(5.2, 4))
    ax.bar(conds, pre, color=COL_PRE, label="pre-gen investment (elicitation)")
    ax.bar(conds, post, bottom=pre, color=COL_POST, label="post-gen revision")
    ax.errorbar(range(len(conds)), [a + b for a, b in zip(pre, post)], yerr=sem, fmt="none", ecolor="#444", capsize=3, lw=0.8)
    for i, (a, b) in enumerate(zip(pre, post)):
        ax.text(i, a + b + (sem[i] or 0), f"{a + b:.0f}s (n={n_by[i]})", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("net human time (s), mean ± SEM")
    ax.set_title("Effort reallocation across conditions" + (f"  [{population}]" if population else ""))
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def fig_dv(pt: pd.DataFrame, dv: str, out: Path, population: str = "") -> None:
    """主 DV 分条件:箱线 + 被试内连线(配对设计标准画法)。"""
    wide = pt.pivot_table(index="participant_id", columns="condition", values=dv)
    # 某条件整列全 NaN(试测期常见:E 轮提交后弃答问卷)时 pivot 会丢掉该列,
    # 再按 pt 的条件取列就 KeyError,整个 make figures 崩在这里 → 只取实际有的列并明说
    conds = [c for c in _cond_order(pt) if c in wide]
    if miss := [c for c in _cond_order(pt) if c not in wide]:
        print(f"({dv}: 条件 {miss} 该列全为 NaN,图中省略)")
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
    ax.set_title(f"{dv} by condition (within-subject)" + (f"  [{population}]" if population else ""))
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def fig_diversity(div: pd.DataFrame, out: Path) -> None:
    conds = [c for c in ORDER if c in div["condition"].unique()]
    # (condition, topic) 格各有自己的 n:按 n 加权,别把 3 稿的格和 12 稿的格平均成一个数
    means = [float(np.average(div.loc[div.condition == c, "gzip_cr"], weights=div.loc[div.condition == c, "n"])) for c in conds]
    fig, ax = plt.subplots(figsize=(4.6, 3.8))
    ax.bar(conds, means, color="#8a9a5b")
    ax.set_ylabel("gzip compression ratio (higher = more homogeneous)")
    ax.set_title("Output homogeneity by condition")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def dump_fig4_material(df: pd.DataFrame, out: Path) -> None:
    """fig4(D 的修改请求 ↔ E 的引导维度)的编码原料。码本尚未定义(paper/8 待办),
    这里只把待编码的原文导出成 CSV 供人工/LLM 编码,不臆造类别。"""
    dims = {}
    for _, r in df[df["condition"] == "E"].iterrows():
        rounds = v3._loads(r.get("guidance_json"), {}).get("rounds") or []
        dims[r["participant_id"]] = "|".join(
            str(it.get("dimension", "")) for rd in rounds for it in (rd.get("items") or []))
    rows = []
    for _, r in df[df["condition"] == "D"].iterrows():
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


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="A6 图表")
    ap.add_argument("--demo", action="store_true", help="只用合成数据渲染验证;不碰真库、文件名带 demo_ 前缀")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    # 与 make stats 同口径:默认 all(主分析人群 = 全样本,2026-09-07),图题标人群。
    # novice 子集图为**探索性**,图题须标 exploratory。以前出图这条路完全不筛、也不标,
    # 而进论文的正是图。
    ap.add_argument("--population", choices=("all", "novice"), default=DEFAULT_POPULATION)
    args = ap.parse_args(argv)
    FIGDIR.mkdir(parents=True, exist_ok=True)

    if args.demo:
        # demo 只出合成图、不碰真库 —— 以前 CSV 缺失时静默退回合成数据,却又照常从真库出 fig4/多样性,
        # 同一个目录里合成图与真图混放。
        pt = _demo_df()
        fig_effort(pt, FIGDIR / "demo_fig_effort.png", "synthetic demo")
        fig_dv(pt, "ownership_composite", FIGDIR / "demo_fig_ownership.png", "synthetic demo")
        print("demo 图已写入", FIGDIR)
        return
    if not CSV.exists():
        raise SystemExit(f"未找到 {CSV} —— 先跑 make v3(或用 --demo 看合成图)。不再静默退回合成数据。")
    # 复合终点只在 stats.py 内存里构建、从不写回 CSV,故这里自己算(否则主 DV 图永远出不来)
    pt = build_composites(pd.read_csv(CSV))
    if args.population == "novice":
        if "novice" not in pt.columns:
            raise SystemExit("CSV 里没有 novice 列 —— 用当前版本的 analysis/v3.py 重新生成")
        pt = pt[pt["novice"].astype(bool)]
    pop = f"{args.population}, n={pt['participant_id'].nunique()}"
    print(f"人群 = {pop}")

    fig_effort(pt, FIGDIR / "fig_effort.png", pop)
    for dv, name in (("ownership_composite", "fig_ownership.png"),
                     ("fidelity_composite", "fig_fidelity.png")):
        if dv in pt and pt[dv].notna().any():
            fig_dv(pt, dv, FIGDIR / name, pop)
        else:
            print(f"(缺 {dv},跳过该图)")

    if args.db.exists():  # 多样性/fig4 原料要回到库里取成稿原文
        df = v3.load(args.db)
        if args.population == "novice" and "novice" in df.columns:
            df = df[df["novice"].astype(bool)]
        div = v3.diversity_by_group(df)
        if len(div):
            fig_diversity(div, FIGDIR / "fig_diversity.png")
        else:
            print("(每组 <2 稿,跳过多样性图)")
        dump_fig4_material(df, CSV.parent / "fig4_coding_material.csv")
    print("图已写入", FIGDIR, "→", ", ".join(p.name for p in sorted(FIGDIR.glob("*.png"))))


if __name__ == "__main__":
    main()
