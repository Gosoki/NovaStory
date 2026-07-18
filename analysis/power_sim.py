#!/usr/bin/env python
"""A6: 合成数据 + 模拟功效分析(无 pilot → SESOI + N=36 先验功效;paper/14 §4)。

双用途:
  (1) simulate():生成 v3 形状的合成 per-trial 数据(被试内 3 条件×3 题×3×3 拉丁方,
      注入已知条件效应)——供 stats.py 端到端自测「能否复原注入的 E−D 效应」。
  (2) 功效:报告 N=36 在给定 SESOI(以配对 dz 计)下的功效,及 80% 功效的最小可检出
      效应(MDES)。因无 pilot,不用 pilot 效应量,改以 SESOI + a priori 模拟。

用法: .venv/bin/python analysis/power_sim.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONDS = ("C", "D", "E")
_COND_ORDERS = (("C", "D", "E"), ("D", "E", "C"), ("E", "C", "D"))
_TOPIC_ORDERS = ((0, 1, 2), (1, 2, 0), (2, 0, 1))


def _plan(seq: int):
    """第 seq 号序列的 [(round_idx, condition, topic)]×3(3×3 拉丁方,与 state.py 同构)。"""
    co, to = _COND_ORDERS[seq // 3 % 3], _TOPIC_ORDERS[seq % 3]
    return [(ri + 1, co[ri], to[ri]) for ri in range(3)]


def simulate(n_subj: int = 36, cond_delta: dict | None = None, subj_sd: float = 1.0,
             resid_sd: float = 1.0, topic_sd: float = 0.3, order_sd: float = 0.15,
             seed: int = 0) -> pd.DataFrame:
    """合成 per-trial 数据。cond_delta = 各条件相对基线的均值偏移(原始单位,D 通常设 0)。
    模型:dv = cond_delta[c] + 被试截距 + 题目效应 + 顺序(练习)效应 + 残差。"""
    rng = np.random.default_rng(seed)
    cond_delta = cond_delta or {"C": -0.3, "D": 0.0, "E": 0.5}
    topic_eff = {t: rng.normal(0, topic_sd) for t in (0, 1, 2)}
    rows = []
    for s in range(n_subj):
        subj = rng.normal(0, subj_sd)
        for ri, c, t in _plan(s % 9):
            dv = (cond_delta[c] + subj + topic_eff[t]
                  + order_sd * (ri - 2) + rng.normal(0, resid_sd))
            rows.append({"participant_id": s, "round_idx": ri,
                         "condition": c, "topic": t, "dv": dv})
    return pd.DataFrame(rows)


def power_paired(dz: float, n: int = 36, nsims: int = 3000, alpha: float = 0.05,
                 seed: int = 0) -> float:
    """被试内 E−D 主对比的功效 = 对 n 个配对差(标准化到 dz)做单样本 t 检验的拒绝率。
    dz = 配对差均值 / 配对差标准差(Cohen's dz)。"""
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(nsims):
        diffs = rng.normal(dz, 1.0, n)
        _, p = stats.ttest_1samp(diffs, 0.0)
        hits += p < alpha
    return hits / nsims


def mdes(n: int = 36, target: float = 0.80, alpha: float = 0.05,
         nsims: int = 3000) -> float:
    """target 功效对应的最小可检出配对 dz —— 二分搜索。"""
    lo, hi = 0.05, 1.2
    for _ in range(24):
        mid = (lo + hi) / 2
        if power_paired(mid, n, nsims, alpha) < target:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 3)


def _empirical_dz(delta: float, n: int = 3000) -> float:
    """delta(原始单位)对应的被试内 E−D 配对 dz(从大样本模拟测得)。"""
    big = simulate(n_subj=n, cond_delta={"C": 0.0, "D": 0.0, "E": delta}, seed=7)
    w = big.pivot_table(index="participant_id", columns="condition", values="dv")
    d = (w["E"] - w["D"]).dropna()
    return float(d.mean() / d.std(ddof=1))


def power_lmm(delta: float, n: int = 36, nsims: int = 200, alpha: float = 0.05) -> float:
    """与真实主分析同构的功效:在 simulate() 数据上跑 stats.py 的实际 LMM + Holm,统计
    E−D 主对比 p_holm<alpha 的比例。比 power_paired 更贴主分析(专家指正:自证功效不应
    与分析模型脱钩),但慢(每次拟合一个 LMM)。delta = 注入的 E−D 原始效应。"""
    from analysis import stats  # lazy:避免与 stats 的循环导入
    hits = 0
    for s in range(nsims):
        df = simulate(n_subj=n, cond_delta={"C": 0.0, "D": 0.0, "E": delta}, seed=2000 + s)
        try:
            ed = stats.contrasts(stats.fit_lmm(df, "dv")).set_index("contrast").loc["E-D"]
            hits += float(ed["p_holm"]) < alpha
        except Exception:  # noqa: BLE001
            pass
    return hits / nsims


def main() -> None:
    ap = argparse.ArgumentParser(description="A6 模拟功效 + 合成自测")
    ap.add_argument("--n", type=int, default=36)
    ap.add_argument("--nsims", type=int, default=3000)
    args = ap.parse_args()

    print(f"=== 被试内 E−D 主对比 · 先验功效(N={args.n},α=.05,配对 t)===")
    print(f"{'配对 dz(SESOI)':<18}{'功效':>8}")
    for dz in (0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7):
        print(f"{dz:<20}{power_paired(dz, args.n, args.nsims):>8.3f}")
    print(f"\n80% 功效的最小可检出效应 MDES(dz) @ N={args.n}: "
          f"{mdes(args.n, 0.80, nsims=args.nsims)}")
    print(f"90% 功效: {mdes(args.n, 0.90, nsims=args.nsims)}")

    print("\n=== 与主分析(LMM+Holm)同构的功效(专家指正:自证模型须=分析模型)===")
    for d0 in (0.4, 0.5):
        dz0 = _empirical_dz(d0)
        print(f"注入 E−D delta={d0}(≈配对 dz={dz0:.2f}) → LMM E−D 主对比功效 "
              f"= {power_lmm(d0, args.n, nsims=150):.3f}")
    print("\n解读:配对 t 近似与 LMM 同构估计一致——N=36 约在 80% 功效检出 dz≈0.48-0.5。"
          "\n⚠️ SESOI 须用本域(创作 HCI)可辩护的最小实质效应,勿直接搬 Maier/APE 的"
          " between-d(跨设计跨域);between-d→within-dz 需条件间相关 ρ 作敏感性。无 pilot,"
          "以上为先验设定,写入预注册。")


if __name__ == "__main__":
    main()
