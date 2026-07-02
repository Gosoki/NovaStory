#!/usr/bin/env python
# ============================================================================
# ⚠️ v2(ModeMirror/HLZ)时代脚本 — 与 v3 schema 系统性脱节,收数前必须重写。
# 已知问题(2026-07-02 第三轮审查):主 DV 绑定已废弃的 hlz_z;读 v3 中不存在/
# 恒空的 v2 列;v3 新列(guidance_json/t_pregen/t_postgen/n_*)与 questionnaires
# 表(主观量表=主要终点)完全没进管线。重写待 A4(主要终点组合)拍板后进行。
# 详见 paper/8「分析层」。
# ============================================================================
"""T7.6 模拟功效分析 — 配对 t 检验的功效曲线(预注册附件)。

HLZ 的方差结构来自真实数据时:用 metrics_per_trial.csv(由 ghost/baseline
管线产出的 per-trial HLZ)估计被试级配对差的标准差;无真实数据时
--synthetic 用 sd=1.0(dz 单位)演示。

对效应量 dz ∈ [0.3, 0.8] × N ∈ [20, 40] 网格,各模拟 n-sims 次配对 t 检验
(双侧 α=.05),输出 data/analysis/power_sim.csv + power_sim.md。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = ROOT / "data" / "analysis"


def estimate_sd_diff(per_trial: pd.DataFrame) -> tuple[float, str]:
    """从 per-trial HLZ 估计被试级配对差(D−C / E−D 合并)的 SD。"""
    wide = (
        per_trial.dropna(subset=["hlz_z"])
        .pivot_table(index="participant_id", columns="condition", values="hlz_z")
    )
    diffs: list[float] = []
    for a, b in (("D", "C"), ("E", "D")):
        if a in wide.columns and b in wide.columns:
            d = (wide[a] - wide[b]).dropna()
            diffs.extend(d.tolist())
    if len(diffs) < 3:
        raise ValueError(f"配对差不足(n={len(diffs)})")
    sd = float(np.std(diffs, ddof=1))
    return sd, f"由 {len(diffs)} 个被试级配对差估计(metrics_per_trial.csv)"


def simulate_power(
    dz: float, n: int, sd_diff: float, n_sims: int, rng: np.random.Generator
) -> float:
    """模拟配对 t 检验功效:diff ~ N(dz·sd, sd),双侧 α=.05。"""
    from scipy import stats as ss

    diffs = rng.normal(dz * sd_diff, sd_diff, size=(n_sims, n))
    m = diffs.mean(axis=1)
    s = diffs.std(axis=1, ddof=1)
    t = m / (s / np.sqrt(n))
    crit = ss.t.ppf(0.975, df=n - 1)
    return float(np.mean(np.abs(t) >= crit))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="模拟功效分析(配对 t,dz 0.3-0.8 × N 20-40)→ power_sim.md/csv"
    )
    ap.add_argument(
        "--synthetic", action="store_true",
        help="无真实数据时用合成参数(sd_diff=1.0)演示",
    )
    ap.add_argument("--sims", type=int, default=2000, help="每格模拟次数(默认 2000)")
    ap.add_argument("--dz-grid", default="0.3,0.4,0.5,0.6,0.7,0.8")
    ap.add_argument("--n-grid", default="20,25,30,35,40")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    dz_grid = [float(x) for x in args.dz_grid.split(",")]
    n_grid = [int(x) for x in args.n_grid.split(",")]

    per_trial_path = ANALYSIS_DIR / "metrics_per_trial.csv"
    if args.synthetic:
        sd_diff, source = 1.0, "合成参数(--synthetic,sd_diff=1.0)"
    elif per_trial_path.exists():
        try:
            sd_diff, source = estimate_sd_diff(pd.read_csv(per_trial_path))
        except ValueError as e:
            sys.exit(f"真实数据不足以估计方差({e})— 可用 --synthetic 演示")
    else:
        sys.exit(f"{per_trial_path} 不存在 — 先跑 metrics.py,或用 --synthetic 演示")

    rng = np.random.default_rng(args.seed)
    rows = [
        {"dz": dz, "N": n, "power": simulate_power(dz, n, sd_diff, args.sims, rng)}
        for dz in dz_grid
        for n in n_grid
    ]
    df = pd.DataFrame(rows)

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = ANALYSIS_DIR / "power_sim.csv"
    df.to_csv(csv_path, index=False)

    pivot = df.pivot(index="dz", columns="N", values="power")
    lines = [
        "# 模拟功效分析(配对 t 检验,双侧 α=.05)",
        "",
        f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"- 方差来源: {source}(sd_diff = {sd_diff:.3f})",
        f"- 每格模拟次数: {args.sims};随机种子: {args.seed}",
        "",
        "| dz \\ N | " + " | ".join(str(n) for n in pivot.columns) + " |",
        "|---" * (len(pivot.columns) + 1) + "|",
    ]
    for dz, row in pivot.iterrows():
        lines.append(
            f"| {dz:.1f} | " + " | ".join(f"{v:.3f}" for v in row) + " |"
        )
    md_path = ANALYSIS_DIR / "power_sim.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"功效网格 {len(df)} 格 → {csv_path}{md_path}")


if __name__ == "__main__":
    main()
