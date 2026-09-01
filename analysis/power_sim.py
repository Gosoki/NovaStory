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
# mirror core/state.py: 6 condition orders (Williams) × 3 topic rotations = 18 seqs.
_COND_ORDERS = (
    ("C", "D", "E"), ("C", "E", "D"),
    ("D", "C", "E"), ("D", "E", "C"),
    ("E", "C", "D"), ("E", "D", "C"),
)
_TOPIC_ORDERS = ((0, 1, 2), (1, 2, 0), (2, 0, 1))
_N_SEQ = len(_COND_ORDERS) * len(_TOPIC_ORDERS)  # 18


def _plan(seq: int):
    """第 seq 号序列的 [(round_idx, condition, topic)]×3(Williams 18 seq,与 state.py 同构)。"""
    co, to = _COND_ORDERS[seq // 3], _TOPIC_ORDERS[seq % 3]
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
        for ri, c, t in _plan(s % _N_SEQ):
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


# ---- novice 子集功效(2026-09-01 拍板 2.2;B1/B2 明写的连带动作,至今未做)----
# 主分析人群 = novice 子集(B1),它的 N 远小于全样本 —— 招 36 人、novice 占比 50% 就只剩 18。
# 此前整个 power_sim 只按 N=36 报,于是对外宣传的「N=36 可检出 dz≈0.48」说的是**全样本**,
# 而主分析根本不在全样本上跑。下面这条曲线才是决定「要招多少人」的那条。
SUBSET_NS = (15, 18, 22, 27, 36)

# 全样本招募量 → novice 子集 N 的换算(用于倒推超招募量)。
# 占比取自 prereg 的 pilot go/no-go 阈值:0.60 = 🟢 线,0.40 = 🟡/🔴 线。
SUBSET_SHARES = (0.60, 0.50, 0.40)


def subset_power_table(nsims: int) -> None:
    """按 novice 子集 N 报功效曲线 + MDES,并倒推达标所需的全样本招募量。"""
    print("\n=== 【主分析人群】novice 子集 · 先验功效(α=.05,配对 t)===")
    print("    B1:主分析跑 novice 子集,全样本只作稳健性 → 决定招募量的是这张表,不是上面那张。")
    header = f"{'子集 N':<8}" + "".join(f"{f'dz={dz}':>9}" for dz in (0.3, 0.4, 0.5, 0.6, 0.7))
    print(header)
    for n in SUBSET_NS:
        row = "".join(f"{power_paired(dz, n, nsims):>9.3f}" for dz in (0.3, 0.4, 0.5, 0.6, 0.7))
        print(f"{n:<8}{row}")

    print(f"\n{'子集 N':<8}{'MDES(80%)':>12}{'MDES(90%)':>12}")
    mdes80 = {}
    for n in SUBSET_NS:
        m80 = mdes(n, 0.80, nsims=nsims)
        mdes80[n] = m80
        print(f"{n:<8}{m80:>12}{mdes(n, 0.90, nsims=nsims):>12}")

    print("\n=== 倒推:要让 novice 子集达到某个 N,全样本得招多少人 ===")
    print("    (再按流失率上浮;11 rank3 按 20% 流失估算过,故最后一列 = 招募目标)")
    print(f"{'子集 N':<8}{'MDES(80%)':>11}" + "".join(f"{f'占比{int(p*100)}%':>12}" for p in SUBSET_SHARES))
    for n in SUBSET_NS:
        cells = "".join(f"{int(-(-n // p)):>7} → {int(-(-(-(-n // p)) // 0.8)):>3}"
                        for p in SUBSET_SHARES)
        print(f"{n:<8}{mdes80[n]:>11}{cells}")
    print("    读法:『子集N → 全样本N → 含20%流失的招募目标』。")
    print("    ⚠️ 这张表出来之后必须做两件事:①把选定的子集目标 N 写进 prereg;")
    print("       ②若倒推出的招募目标不现实,**现在**就改成 4-of-5"
          "(prereg.NOVICE_MIN_CRITERIA=4)或改『novice 为主体 + 经验作调节』——不能看了数据再改。")


def main() -> None:
    ap = argparse.ArgumentParser(description="A6 模拟功效 + 合成自测")
    ap.add_argument("--n", type=int, default=36)
    ap.add_argument("--nsims", type=int, default=3000)
    ap.add_argument("--subset-only", action="store_true",
                    help="只跑 novice 子集功效表(跳过全样本与 LMM 同构自测,快)")
    args = ap.parse_args()

    if args.subset_only:
        subset_power_table(args.nsims)
        return

    print(f"=== 全样本 · 被试内 E−D 主对比 · 先验功效(N={args.n},α=.05,配对 t)===")
    print("    ⚠️ 这是**稳健性**人群的数字。主分析人群见下方 novice 子集表(B1)。")
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
    subset_power_table(args.nsims)

    print("\n解读:配对 t 近似与 LMM 同构估计一致——全样本 N=36 约在 80% 功效检出 dz≈0.48-0.5;"
          "\n     **主分析的 novice 子集 N 更小,需要更大的 dz** —— 以上方子集表为准。"
          "\n⚠️ SESOI 须用本域(创作 HCI)可辩护的最小实质效应,勿直接搬 Maier/APE 的"
          " between-d(跨设计跨域);between-d→within-dz 需条件间相关 ρ 作敏感性。无 pilot,"
          "以上为先验设定,写入预注册。")


if __name__ == "__main__":
    main()
