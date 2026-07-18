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

CSV = ROOT / "data" / "analysis" / "v3_per_trial.csv"
_PAIRS = [("E", "D"), ("E", "C"), ("D", "C")]  # E−D 为主


# ---------------- 复合终点 ----------------

def _z(s: pd.Series) -> pd.Series:
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd else s * 0.0


def build_composites(df: pd.DataFrame) -> pd.DataFrame:
    """保真复合 = z(想象匹配)+z(违背取反)+z(逐镜头 mine 比)+z(embedding Δ,若有) 的均值;
    所有权复合 = own_mean(paper/14 §2、A4)。缺哪项跳哪项。"""
    df = df.copy()
    parts, names = [], []
    for col, sign in (("imagine", 1), ("violation", -1), ("mine_ratio", 1), ("embed_fidelity", 1)):
        if col in df:
            parts.append(sign * _z(df[col]))
            names.append(col)
    if parts:
        _warn_uneven_coverage(df, names)
        df["fidelity_composite"] = pd.concat(parts, axis=1).mean(axis=1)
    if "own_mean" in df:
        df["ownership_composite"] = df["own_mean"]
    return df


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

def fit_lmm(df: pd.DataFrame, dv: str):
    import statsmodels.formula.api as smf
    d = df.dropna(subset=[dv, "condition"]).copy()
    d["order"] = d["round_idx"].astype(float)
    formula = f"Q('{dv}') ~ C(condition) + C(topic) + order"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return smf.mixedlm(formula, d, groups=d["participant_id"]).fit(method="lbfgs")


def _holm(pvals: list[float]) -> list[float]:
    m = len(pvals)
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


def tost(df: pd.DataFrame, dv: str, pair=("E", "D"), bound: float = 0.5) -> dict:
    """等价/非劣检验:E 与 D 在 dv 上是否等价(|差| < bound)。
    **bound 必须是预注册的 a priori SESOI**(dv 原始/z 单位的绝对值),与观测数据无关。
    切勿用样本自身配对差 SD 现算 bound——那会让等价界随抽样噪声漂移、Type I 失控
    (代码审查 + 统计专家一致指正)。每个终点在预注册里按源量表/文献设定各自 bound。"""
    wide = _paired(df, dv)
    a, b = pair
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


def dose_response(df: pd.DataFrame, dv: str, dose: str = "pre_investment") -> dict:
    """E 内:事前投入 → 保真(被试间 OLS;每被试仅 1 个 E,无法被试内中心化——附注局限)。"""
    import statsmodels.formula.api as smf
    e = df[(df["condition"] == "E")].dropna(subset=[dv, dose])
    if len(e) < 8 or e[dose].std() == 0:
        return {"n": len(e), "note": "样本不足/无方差"}
    m = smf.ols(f"Q('{dv}') ~ Q('{dose}')", e).fit()
    return {"n": int(len(e)), "beta": float(m.params.iloc[1]),
            "p": float(m.pvalues.iloc[1]), "r2": float(m.rsquared)}


# ---------------- 端点分析 + CLI ----------------

def analyze_endpoint(df: pd.DataFrame, dv: str) -> None:
    print(f"\n########## 端点: {dv} ##########")
    if dv not in df or df[dv].notna().sum() < 6:
        print("  (数据不足,跳过)")
        return
    means = df.groupby("condition")[dv].agg(["mean", "std", "count"])
    print("按条件:\n", means.round(3).to_string())
    try:
        fit = fit_lmm(df, dv)
        print("\nLMM 计划对比(Holm;E−D 为主):")
        print(contrasts(fit).round(4).to_string(index=False))
    except Exception as e:  # noqa: BLE001
        print("  LMM 失败:", e)
    wp = wilcoxon_pairs(df, dv)
    if len(wp):
        print("\nWilcoxon 配对(稳健):\n", wp.round(4).to_string(index=False))


def _demo() -> None:
    from analysis import power_sim
    delta = {"C": -0.3, "D": 0.0, "E": 0.5}
    print("=== ① 单个 N=36 数据集(真实规模,有噪声):注入 E−D=+0.5 ===")
    df = power_sim.simulate(n_subj=36, cond_delta=delta, seed=1)
    analyze_endpoint(df, "dv")
    print("\nTOST 等价示例(E vs C, bound=0.5 绝对/预注册 SESOI):", tost(df, "dv", ("E", "C")))

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

    df = build_composites(pd.read_csv(args.csv))
    for dv in ("ownership_composite", "fidelity_composite", "satisfaction",
               "post_investment", "total_investment"):
        analyze_endpoint(df, dv)
    print("\n剂量-反应(E 内 事前投入→保真):",
          dose_response(df, "fidelity_composite") if "fidelity_composite" in df else "无")
    print("\n注:embedding 相对基线保真 Δ 由 embed.py 合入后进保真复合;质量走 TOST 非劣。")


if __name__ == "__main__":
    main()
