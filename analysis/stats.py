#!/usr/bin/env python
"""A6: 推断统计(paper/10 §7.1 + paper/14 §5)。

主分析: 线性混合模型 LMM  DV ~ 条件 + 题目 + 顺序位置 + (1|被试);
计划对比 E−D(主)/ E−C / D−C,族内 Holm 校正。
等价:  TOST(质量「不劣于」的非劣主张)。稳健: Wilcoxon 配对符号秩。
剂量-反应: E 内 事前投入 → 保真(被试间,附注局限)。

输入: analysis/v3.py 的 per-trial CSV(缺主复合成分则跳过该复合)。
无数据时 `--demo` 用 power_sim 的合成数据自测:能否复原注入的 E−D 效应。

用法: .venv/bin/python analysis/stats.py            # 有 CSV 则跑真数据,否则自测
      .venv/bin/python analysis/stats.py --demo     # 强制合成自测
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import prereg  # noqa: E402  (预注册常量单一真源:终点层级 / SESOI)

CSV = ROOT / "data" / "analysis" / "v3_per_trial.csv"
_PAIRS = [("E", "D"), ("E", "C"), ("D", "C")]  # E−D 为主
_FIDELITY_LEGS = ("imagine", "violation", "mine_ratio", "embed_fidelity")
_EFFORT_LEGS = ("n_ai_rounds", "hand_edit_chars", "post_investment")
_DOSE_LEGS = ("pre_investment", "g_custom_rate", "g_ai_decided_rate")
_H4_QUALITY_DVS = ("field_completeness", "shots_ok")  # H4 结构完整度(客观下界)


# ---------------- 复合终点 ----------------

def _z(s: pd.Series) -> pd.Series:
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd else s * 0.0


def build_composites(df: pd.DataFrame) -> pd.DataFrame:
    """保真复合 = z(想象匹配)+z(违背取反)+z(逐镜头 mine 比)+z(embedding Δ,若有) 的均值;
    所有权复合 = own_mean(paper/14 §2、A4)。缺哪项跳哪项。"""
    df = df.copy()
    parts, names = [], []
    for col, sign in zip(_FIDELITY_LEGS, (1, -1, 1, 1)):
        if col in df and df[col].notna().any():  # 整列全 NaN 等同缺失(否则悄悄少一条腿)
            parts.append(sign * _z(df[col]))
            names.append(col)
    if parts:
        _warn_uneven_coverage(df, names)
        _warn_missing_legs("fidelity_composite", _FIDELITY_LEGS, names)
        df["fidelity_composite"] = pd.concat(parts, axis=1).mean(axis=1)
    if "own_mean" in df:
        df["ownership_composite"] = df["own_mean"]
    # H3a 努力再分配(事后返工复合):n_ai_rounds + 手改字符 + t_postgen。计数/时长右偏,
    # 先 log1p 再 z,复合近似对称、可进高斯 LMM(paper/10 §7.1:计数→负二项、时长→log;
    # 复合走 log)。单终点 n_ai_rounds 的确证检验仍应负二项——预注册 SAP 锁定。
    # (深度评审 2026-07-19 #10:此前从未构建,招牌图 H3a 的推断缺一半。)
    eff, eff_names = [], []
    for col in _EFFORT_LEGS:
        if col in df and df[col].notna().any():
            eff.append(_z(np.log1p(pd.to_numeric(df[col], errors="coerce").clip(lower=0))))
            eff_names.append(col)
    if eff:
        _warn_missing_legs("effort_composite", _EFFORT_LEGS, eff_names)
        df["effort_composite"] = pd.concat(eff, axis=1).mean(axis=1)
    return df


def build_dose(df: pd.DataFrame) -> pd.DataFrame:
    """H5 剂量复合(paper/10 §7.1:自填率 + 答题净时 + 1−AI代答率)= 三者 z 的均值。
    仅 E 有引导数据,故在 E 子集内 z;z(1−x) ≡ −z(x),故 ai_decided 取负号。缺哪项跳哪项
    (g_* 列由 v3.py 产出,若尚未落盘则退化为在场成分)。"""
    df = df.copy()
    e = df["condition"] == "E"
    parts, names = [], []
    for col, sign in zip(_DOSE_LEGS, (1, 1, -1)):
        if col in df and df.loc[e, col].notna().any():
            parts.append(sign * _z(df.loc[e, col]))
            names.append(col)
    if parts:
        _warn_missing_legs("dose_composite", _DOSE_LEGS, names)
        df.loc[e, "dose_composite"] = pd.concat(parts, axis=1).mean(axis=1)
    return df


def _warn_missing_legs(name: str, full: tuple, present: list[str]) -> None:
    """复合按不足额的成分集构建时告警(全 NaN 的腿覆盖率均匀为 0,_warn_uneven_coverage 抓不到,
    复合会静默降为少数腿——深度评审 A5)。"""
    miss = [c for c in full if c not in present]
    if miss:
        warnings.warn(f"{name} 仅由 {len(present)}/{len(full)} 个成分构建,缺 {miss};"
                      "与预注册复合定义不一致,结果须按实际成分集报告。")


def _warn_uneven_coverage(df: pd.DataFrame, cols: list[str]) -> None:
    """复合成分若非空覆盖率随条件差异大,skipna 行均值会让各条件落在不同成分集/参照总体,
    主对比有偏(代码审查 stats.py:50)。正常设计下各成分对 C/D/E 均齐,故仅异常时告警。"""
    if "condition" not in df:
        return
    for c in cols:
        cov = df.groupby("condition")[c].apply(lambda s: s.notna().mean())
        if len(cov) > 1 and float(cov.max() - cov.min()) > 0.2:
            warnings.warn(f"复合成分 {c} 非空覆盖率随条件差异大 {cov.round(2).to_dict()};"
                          "复合终点可能有偏,请统一成分集合或分层报告。")


# ---------------- LMM + 计划对比 ----------------

_OPTIMIZERS = ("lbfgs", "powell", "bfgs", "nm", "cg")


def fit_lmm(df: pd.DataFrame, dv: str):
    """依次试 _OPTIMIZERS,返回第一个拟合成功的。lbfgs 在本设计上常抛 Singular matrix
    而 powell/bfgs/nm 拟合同一模型无碍(N=36 合成数据 6 个终点里 5 个如此),写死单一
    优化器会让预注册的确证分析静默消失。全失败则抛,绝不返回 None。"""
    import statsmodels.formula.api as smf
    d = df.dropna(subset=[dv, "condition"]).copy()
    d["order"] = d["round_idx"].astype(float)
    formula = f"Q('{dv}') ~ C(condition) + C(topic) + order"
    errs = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for opt in _OPTIMIZERS:
            try:
                return smf.mixedlm(formula, d, groups=d["participant_id"]).fit(method=opt)
            except Exception as e:  # noqa: BLE001
                errs.append(f"{opt}={type(e).__name__}: {e}")
    raise RuntimeError(f"{dv}: 全部优化器均失败(" + "; ".join(errs) + ")")


def _holm(pvals: list[float]) -> list[float]:
    m = len(pvals)
    pvals = [p if np.isfinite(p) else 1.0 for p in pvals]  # 退化拟合的 NaN p 不得被排成 0(假显著)
    order = np.argsort(pvals)
    adj, run = [1.0] * m, 0.0
    for rank, idx in enumerate(order):
        run = max(run, (m - rank) * pvals[idx])
        adj[idx] = min(run, 1.0)
    return adj


def contrasts(fit) -> pd.DataFrame:
    """三个计划对比(条件参考为 C:T.D=D−C、T.E=E−C;E−D=T.E−T.D)。Holm 校正。
    MixedLM.t_test 需长度 = 固定效应数的数值对比向量。"""
    names = list(fit.model.exog_names)
    k, iD, iE = len(names), names.index("C(condition)[T.D]"), names.index("C(condition)[T.E]")

    def vec(pos_hi, pos_lo=None):
        v = np.zeros(k)
        v[pos_hi] = 1.0
        if pos_lo is not None:
            v[pos_lo] = -1.0
        return v

    specs = {"E-D": vec(iE, iD), "E-C": vec(iE), "D-C": vec(iD)}
    rows = []
    for name, v in specs.items():
        r = fit.t_test(v.reshape(1, -1))
        rows.append({"contrast": name, "estimate": float(np.ravel(r.effect)[0]),
                     "se": float(np.ravel(r.sd)[0]), "p_raw": float(np.ravel(r.pvalue)[0])})
    out = pd.DataFrame(rows)
    out["p_holm"] = _holm(out["p_raw"].tolist())
    return out


# ---------------- 配对稳健 + 等价 ----------------

def _paired(df: pd.DataFrame, dv: str) -> pd.DataFrame:
    return df.pivot_table(index="participant_id", columns="condition", values=dv)


def wilcoxon_pairs(df: pd.DataFrame, dv: str) -> pd.DataFrame:
    wide = _paired(df, dv)
    rows = []
    for a, b in _PAIRS:
        if a in wide and b in wide:
            d = (wide[a] - wide[b]).dropna()
            if len(d) >= 5 and d.abs().sum() > 0:
                w, p = stats.wilcoxon(d)
                rows.append({"pair": f"{a}-{b}", "n": len(d),
                             "median_diff": float(d.median()), "p": float(p)})
    return pd.DataFrame(rows)


def tost(df: pd.DataFrame, dv: str, pair=("E", "D"), bound: float | None = None) -> dict:
    """等价/非劣检验:E 与 D 在 dv 上是否等价(|差| < bound)。
    **bound 必须是预注册的 a priori SESOI**(dv 原始/z 单位的绝对值),与观测数据无关;
    默认取 prereg.SESOI(单一真源)。切勿用样本自身配对差 SD 现算 bound——那会让等价界
    随抽样噪声漂移、Type I 失控(代码审查 + 统计专家一致指正)。
    SESOI 未锁定时**拒绝执行**,不退回任何默认值。"""
    if bound is None:
        bound = prereg.SESOI
    if bound is None:
        print("!" * 78)
        print(f"⛔ TOST 拒绝执行(H4 非劣,dv={dv},{pair[0]}−{pair[1]}):prereg.SESOI 仍为 None。")
        print(f"   预注册前研究员必须拍板:{dv} 上多大的 {pair[0]}−{pair[1]} 差异才算「实质劣于」")
        print("   (即等价界 SESOI,用 DV 原始单位:结构完整度是 0-1 比例,如 0.05 = 5 个百分点),")
        print("   并把该数值写入 analysis/prereg.py 的 SESOI。在此之前 H4 无结论,不得用默认界代替。")
        print("!" * 78)
        return {"pair": f"{pair[0]}-{pair[1]}", "dv": dv, "equivalent": None,
                "note": "prereg.SESOI 未锁定,拒绝执行"}
    wide = _paired(df, dv)
    a, b = pair
    if a not in wide or b not in wide:  # 该条件整列缺/全 NaN(试测期 E 未跑、解析全失败)
        return {"pair": f"{a}-{b}", "dv": dv, "equivalent": None, "note": f"缺条件 {a}/{b} 的配对数据"}
    d = (wide[a] - wide[b]).dropna()
    n = len(d)
    if n < 5:
        return {"pair": f"{a}-{b}", "n": n, "equivalent": None}
    m, sd = d.mean(), d.std(ddof=1)
    if sd == 0 or bound <= 0:  # 零方差/无效界 → 除零;非劣无法判定
        return {"pair": f"{a}-{b}", "n": n, "mean_diff": float(m),
                "equivalent": None, "note": "配对差零方差 / SESOI 无效"}
    se = sd / np.sqrt(n)
    p_lower = stats.t.sf((m + bound) / se, n - 1)   # H1: 差 > −bound
    p_upper = stats.t.cdf((m - bound) / se, n - 1)  # H1: 差 < +bound
    return {"pair": f"{a}-{b}", "n": n, "mean_diff": float(m), "bound": float(bound),
            "p_lower": float(p_lower), "p_upper": float(p_upper),
            "equivalent": bool(p_lower < .05 and p_upper < .05)}


def dose_response(df: pd.DataFrame, dv: str, dose: str = "dose_composite") -> dict:
    """E 内:事前投入 → 保真(被试间 OLS;每被试仅 1 个 E,无法被试内中心化——附注局限)。
    默认剂量为 build_dose 的三成分复合;dose='pre_investment' 可得只看时间的可比版本。"""
    import statsmodels.formula.api as smf
    if dose not in df:
        return {"note": f"缺剂量列 {dose}"}
    e = df[(df["condition"] == "E")].dropna(subset=[dv, dose])
    if len(e) < 8 or e[dose].std() == 0:
        return {"n": len(e), "note": "样本不足/无方差"}
    m = smf.ols(f"Q('{dv}') ~ Q('{dose}')", e).fit()
    return {"n": int(len(e)), "beta": float(m.params.iloc[1]),
            "p": float(m.pvalues.iloc[1]), "r2": float(m.rsquared)}


# ---------------- 端点分析 + CLI ----------------

def analyze_endpoint(df: pd.DataFrame, dv: str) -> str | None:
    """跑完一个终点;返回 None 表示确证分析(LMM+计划对比+Holm)已产出,否则返回失败原因。
    LMM 失败不中断整轮,但必须在输出里大声报错并被 main 汇总——否则确证分析悄悄消失。"""
    print(f"\n########## 端点: {dv} ##########")
    if dv not in df or df[dv].notna().sum() < 6:
        print("  (数据不足,跳过)")
        return "数据不足(该列缺失或非空 < 6)"
    means = df.groupby("condition")[dv].agg(["mean", "std", "count"])
    print("按条件:\n", means.round(3).to_string())
    err = None
    try:
        fit = fit_lmm(df, dv)
        con = contrasts(fit)
        if not np.isfinite(con[["se", "p_raw"]].to_numpy()).all():
            # 零方差/共线 DV 上 LMM 会「成功」但 se/p 全 NaN,照样打印就成了假的确证结论
            raise RuntimeError(f"{dv}: LMM 退化,计划对比 se/p 非有限值(DV 无方差或与设计共线)")
        print("\nLMM 计划对比(Holm;E−D 为主):")
        print(con.round(4).to_string(index=False))
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        print("!" * 78)
        print(f"⛔ LMM 失败 → 预注册确证分析(计划对比 + Holm)缺失!终点 = {dv}")
        print(f"   {err}")
        print("   以下 Wilcoxon 只是稳健性回退,不能当作该终点的确证结论。")
        print("!" * 78)
    wp = wilcoxon_pairs(df, dv)
    if len(wp):
        print("\nWilcoxon 配对(稳健):\n", wp.round(4).to_string(index=False))
    return err


def _demo() -> None:
    from analysis import power_sim
    delta = {"C": -0.3, "D": 0.0, "E": 0.5}
    print("=== ① 单个 N=36 数据集(真实规模,有噪声):注入 E−D=+0.5 ===")
    df = power_sim.simulate(n_subj=36, cond_delta=delta, seed=1)
    analyze_endpoint(df, "dv")
    print("\nTOST 机制自测(E vs C;合成数据上显式给 bound=0.5,不是预注册 SESOI):",
          tost(df, "dv", ("E", "C"), bound=0.5))

    print("\n=== ② 大 N=400 验证管线正确性(应紧密复原注入值)===")
    big = power_sim.simulate(n_subj=400, cond_delta=delta, seed=1)
    con = contrasts(fit_lmm(big, "dv")).set_index("contrast")
    print(con.round(4).to_string())
    ed, ec, dc = con.loc["E-D", "estimate"], con.loc["E-C", "estimate"], con.loc["D-C", "estimate"]
    ok = (abs(ed - 0.5) < .1 and abs(ec - 0.8) < .1 and abs(dc - 0.3) < .1
          and con.loc["E-D", "p_holm"] < .01)
    print(f"\n>> 复原: E−D={ed:.3f}(注 0.5) E−C={ec:.3f}(注 0.8) D−C={dc:.3f}(注 0.3)")
    print("管线自测", "通过 ✅" if ok else "异常 ⚠️")


def main() -> None:
    ap = argparse.ArgumentParser(description="A6 v3 推断统计")
    ap.add_argument("--csv", type=Path, default=CSV)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    if args.demo or not args.csv.exists():
        if not args.csv.exists() and not args.demo:
            print(f"(未找到 {args.csv},改跑合成自测)\n")
        _demo()
        return

    df = build_dose(build_composites(pd.read_csv(args.csv)))
    failed = {}
    for dv in prereg.PRIMARY_ENDPOINTS + prereg.SECONDARY_ENDPOINTS:  # 终点层级单一真源
        err = analyze_endpoint(df, dv)
        if err:
            failed[dv] = err

    print("\n########## H4 质量非劣(TOST,E vs D,结构完整度=客观下界)##########")
    for dv in _H4_QUALITY_DVS:
        if dv in df:
            print(f"  {dv}: {tost(df, dv, ('E', 'D'))}")

    if "fidelity_composite" in df:
        print("\n########## H5 剂量-反应(E 内,被试间 OLS,探索)##########")
        print("  复合剂量(自填率+答题净时+1−AI代答率):", dose_response(df, "fidelity_composite"))
        print("  仅时间(pre_investment,可比参照):",
              dose_response(df, "fidelity_composite", dose="pre_investment"))

    if failed:
        print("\n" + "!" * 78)
        print(f"⛔ 最终警告:{len(failed)} 个预注册终点没有确证结果(LMM+计划对比+Holm 缺失):")
        for dv, err in failed.items():
            print(f"   - {dv}  ←  {err}")
        print("   这些终点当前只有描述性/稳健性输出,不可写进确证性结论。")
        print("!" * 78)
    print("\n注:embedding 相对基线保真 Δ 由 embed.py 合入后进保真复合;质量走 TOST 非劣。")


if __name__ == "__main__":
    main()
