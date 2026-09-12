#!/usr/bin/env python
"""A6: 合成数据 + 模拟功效分析(无 pilot → SESOI + N=36 先验功效;paper/14 §4)。

双用途:
  (1) simulate():生成 v3 形状的合成 per-trial 数据(被试内 3 条件×3 题,Williams 6 排列×3 题目轮转
      = 18 序列,与 core/state.py 同一份表;注入已知条件效应)——供 stats.py 端到端自测。
  (2) 功效:报告 N=36 在给定 SESOI(以配对 dz 计)下的功效,及 80% 功效的最小可检出
      效应(MDES)。因无 pilot,不用 pilot 效应量,改以 SESOI + a priori 模拟。

用法: .venv/bin/python analysis/power_sim.py
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import prereg  # noqa: E402
from core import config  # noqa: E402
from core import state as _state  # noqa: E402  (排列表的唯一真源;以前这里手抄了一份)

CONDS = ("C", "D", "E")
_N_SEQ = config.LATIN_SQUARE_N


def plan_for_seq(seq: int):
    """第 seq 号序列的 [(round_idx, condition, topic_idx)]×3 —— 直接读 core/state 的排列表,
    与被试实际分配同一份(以前是逐字副本,改了 state 这里不会跟着动)。"""
    n_topic = len(_state._TOPIC_ORDERS)
    co, to = _state._COND_ORDERS[seq // n_topic], _state._TOPIC_ORDERS[seq % n_topic]
    return [(ri + 1, co[ri], to[ri]) for ri in range(config.N_ROUNDS)]


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
        for ri, c, t in plan_for_seq(s % _N_SEQ):
            dv = (cond_delta[c] + subj + topic_eff[t]
                  + order_sd * (ri - 2) + rng.normal(0, resid_sd))
            rows.append({"participant_id": s, "round_idx": ri,
                         "condition": c, "topic": t, "dv": dv})
    return pd.DataFrame(rows)


def power_paired(dz: float, n: int = 36, alpha: float = prereg.ALPHA) -> float:
    """被试内 E−D 主对比的功效:n 个配对差(标准化到 dz)的单样本 t 检验,双侧 α。

    **解析解**(非中心 t):正态配对差下是精确值,毫秒级、无随机噪声。以前是蒙特卡洛 ——
    nsims=500 时 ±0.03 的噪声进了一个**决定招募人数**的数字,3000 次又跑不完(实测超时)。
    dz = 配对差均值 / 配对差标准差(Cohen's dz)。"""
    df = n - 1
    tcrit = stats.t.ppf(1 - alpha / 2, df)
    nc = dz * math.sqrt(n)
    return float(stats.nct.sf(tcrit, df, nc) + stats.nct.cdf(-tcrit, df, nc))


def mdes(n: int = 36, target: float = 0.80, alpha: float = prereg.ALPHA) -> float:
    """target 功效对应的最小可检出配对 dz —— 在解析功效上二分。"""
    lo, hi = 0.05, 1.5
    for _ in range(40):
        mid = (lo + hi) / 2
        if power_paired(mid, n, alpha) < target:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 3)


def _simulated_dz(delta: float, n: int = 3000) -> float:
    """delta(原始单位)对应的被试内 E−D 配对 dz(从**合成**大样本测得;本研究 N=0,没有实测 dz)。"""
    big = simulate(n_subj=n, cond_delta={"C": 0.0, "D": 0.0, "E": delta}, seed=7)
    w = big.pivot_table(index="participant_id", columns="condition", values="dv")
    d = (w["E"] - w["D"]).dropna()
    return float(d.mean() / d.std(ddof=1))


def power_lmm(delta: float, n: int = 36, nsims: int = 200, alpha: float = prereg.ALPHA) -> float:
    """与真实主分析同构的功效:在 simulate() 数据上跑 stats.py 的实际 LMM + Holm,统计
    E−D 主对比 p_holm<alpha 的比例。比 power_paired 更贴主分析(专家指正:自证功效不应
    与分析模型脱钩),但慢(每次拟合一个 LMM)。delta = 注入的 E−D 原始效应。"""
    from analysis import stats as A_stats  # lazy:避免与 stats 的循环导入(也别遮蔽 scipy.stats)
    hits, failed = 0, 0
    for s in range(nsims):
        df = simulate(n_subj=n, cond_delta={"C": 0.0, "D": 0.0, "E": delta}, seed=2000 + s)
        try:
            ed = A_stats.contrasts(A_stats.fit_lmm(df, "dv")).set_index("contrast").loc["E-D"]
            hits += float(ed["p_holm"]) < alpha
        except Exception:  # noqa: BLE001
            failed += 1   # 以前静默吞掉并计为「未检出」:LMM 系统性失败会伪装成一个看着正常的低功效
    if failed:
        print(f"    ⚠️ power_lmm:{failed}/{nsims} 次拟合失败(计为未检出),功效被低估")
    return hits / nsims


# ---- novice 子集功效(现为**探索性分析**的功效注记)----
# 2026-09-07 拍板:主分析人群 = 全样本 → 决定招募量的是**上面那张全样本表**,不是这张。
# 这条曲线保留下来,是为了在论文里如实交代:事后按 novice 切分时 N 会小很多
# (招 36 人、占比 50% 就只剩 18),因此那部分结论功效不足,只能作探索性报告。
NOVICE_SUBSET_NS = (15, 18, 22, 27, 36)

# 全样本招募量 → novice 子集 N 的换算情景。
# ⚠️ 2026-09-07 起**不再用于倒推超招募量**(停止规则已去掉「子集也达标」这一条),
#    只用于说明探索性子集在既定招募量下会剩多少人。
# 端点直接读 prereg 的 pilot go/no-go 阈值(🟢 线 / 🟡🔴 线),中间取 0.50;以前手抄了一份。
NOVICE_SHARE_SCENARIOS = (prereg.NOVICE_SHARE_GREEN, 0.50, prereg.NOVICE_SHARE_YELLOW)


def subset_power_table() -> None:
    """按 novice 子集 N 报功效曲线 + MDES(**探索性分析**的功效下限参考)。"""
    print("\n=== 【探索性】novice 子集 · 先验功效(α=.05,配对 t)===")
    print("    主分析人群 = 全样本,功效以上方全样本表为准;这张表只用于交代探索性切分的功效不足。")
    header = f"{'子集 N':<8}" + "".join(f"{f'dz={dz}':>9}" for dz in (0.3, 0.4, 0.5, 0.6, 0.7))
    print(header)
    for n in NOVICE_SUBSET_NS:
        row = "".join(f"{power_paired(dz, n):>9.3f}" for dz in (0.3, 0.4, 0.5, 0.6, 0.7))
        print(f"{n:<8}{row}")

    print(f"\n{'子集 N':<8}{'MDES(80%)':>12}{'MDES(90%)':>12}")
    mdes80 = {}
    for n in NOVICE_SUBSET_NS:
        m80 = mdes(n, 0.80)
        mdes80[n] = m80
        print(f"{n:<8}{m80:>12}{mdes(n, 0.90):>12}")

    print("\n=== 参考:要让 novice 子集达到某个 N,全样本得招多少人 ===")
    print("    ⚠️ 2026-09-07 起这**不是招募目标表** —— 主分析人群是全样本,招募量由上方全样本表决定。")
    print("    (保留它只为回答一个问题:既定招募量下,事后按 novice 切分还剩多少人可用。)")
    print(f"{'子集 N':<8}{'MDES(80%)':>11}" + "".join(f"{f'占比{int(p*100)}%':>12}" for p in NOVICE_SHARE_SCENARIOS))
    for n in NOVICE_SUBSET_NS:
        # ⚠️ 别用 -(-n // p) 当 ceil:p 是浮点,n/p 在二进制里往往差一个 ulp
        # (实测 15/0.6 → 我算 26、正确 25,60% 那一列整列偏 1)。用 math.ceil。
        cells = ""
        for p in NOVICE_SHARE_SCENARIOS:
            full = math.ceil(n / p)              # 子集 N → 需要的全样本 N
            target = math.ceil(full / 0.8)       # 再按 20% 流失上浮 → 招募目标
            cells += f"{full:>7} → {target:>3}"
        print(f"{n:<8}{mdes80[n]:>11}{cells}")
    print("    读法:『子集N → 需要的全样本N → 含20%流失的对应招募量』(仅供参照,非目标)。")
    print("    ⚠️ 反过来读才是现在的用法:既定招募 36/队列 → 子集大概落在哪一行 →"
          "\n       该行 MDES 就是**探索性**切分的功效下限,论文里照此交代。")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="A6 模拟功效 + 合成自测")
    ap.add_argument("--n", type=int, default=36)
    ap.add_argument("--nsims", type=int, default=150, help="LMM 同构功效的蒙特卡洛次数(配对 t 已是解析解,不用它)")
    ap.add_argument("--subset-only", action="store_true",
                    help="只跑 novice 子集功效表(跳过全样本与 LMM 同构自测,快)")
    args = ap.parse_args(argv)

    if args.subset_only:
        subset_power_table()
        return

    print(f"=== 全样本 · 被试内 E−D 主对比 · 先验功效(N={args.n},α={prereg.ALPHA},配对 t,解析解)===")
    print("    ✅ 这就是**主分析人群**(全样本)的数字(2026-09-07 拍板);"
          "下方 novice 子集表仅为探索性分析的功效参考。")
    print(f"{'配对 dz(SESOI)':<18}{'功效':>8}")
    for dz in (0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7):
        print(f"{dz:<20}{power_paired(dz, args.n):>8.3f}")
    print(f"\n80% 功效的最小可检出效应 MDES(dz) @ N={args.n}: {mdes(args.n, 0.80)}")
    print(f"90% 功效: {mdes(args.n, 0.90)}")

    print("\n=== 与主分析(LMM+Holm)同构的功效(专家指正:自证模型须=分析模型)===")
    for d0 in (0.4, 0.5):
        dz0 = _simulated_dz(d0)
        print(f"注入 E−D delta={d0}(≈配对 dz={dz0:.2f}) → LMM E−D 主对比功效 "
              f"= {power_lmm(d0, args.n, nsims=args.nsims):.3f}")
    subset_power_table()

    print("\n解读:配对 t 近似与 LMM 同构估计一致——**主分析(全样本)N=36** 约在 80% 功效"
          "检出 dz≈0.48-0.5;中日两队列合并(N=72)约 dz≈0.33。"
          "\n     novice 子集因 N 更小而功效不足,其结论只作**探索性**报告。"
          "\n⚠️ SESOI 须用本域(创作 HCI)可辩护的最小实质效应,勿直接搬 Maier/APE 的"
          " between-d(跨设计跨域);between-d→within-dz 需条件间相关 ρ 作敏感性。无 pilot,"
          "以上为先验设定,写入冻结产物(scripts/freeze_prereg.py)。")


if __name__ == "__main__":
    main()
