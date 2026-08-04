#!/usr/bin/env python
"""A6: 推断统计(docs/paper/04_假设与分析计划.md §1·§3)。

主分析: 线性混合模型 LMM  DV ~ 条件 + 题目 + 顺序位置 + (1|被试);
计划对比 E−D(主)/ E−C / D−C,族内 Holm 校正。
等价:  TOST(质量「不劣于」的非劣主张,DV = 结构完整度复合)。稳健: Wilcoxon 配对符号秩。
剂量-反应: E 内 事前投入 → 保真 / 所有权(被试间,附注局限)。

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

from analysis import prereg  # noqa: E402  (冻结分析计划常量的单一真源:终点层级 / SESOI)

CSV = ROOT / "data" / "analysis" / "v3_per_trial.csv"
_PAIRS = [("E", "D"), ("E", "C"), ("D", "C")]  # E−D 为主
_FIDELITY_SUBJ_LEGS = ("imagine", "violation", "mine_ratio")   # 主观三腿,先内部平均
_FIDELITY_OBJ_LEG = "embed_fidelity"                          # 唯一的客观腿,占一半
_FIDELITY_LEGS = (*_FIDELITY_SUBJ_LEGS, _FIDELITY_OBJ_LEG)    # 仅用于「缺腿」告警的全集
_EFFORT_LEGS = ("n_ai_rounds", "hand_edit_chars", "post_investment")
_DOSE_LEGS = ("pre_investment", "g_custom_rate", "g_ai_decided_rate")
_H4_LEGS = ("parse_ok", "field_completeness", "spec_ok")   # 三者**合成一个** DV,见 build_quality
# spec_ok = 「15s 且 3 镜」达标(v3.structural);此前用的 shots_ok 只判镜数、从不核验总时长,
# 与 docs/paper/03 §4 写的「15s/3镜达标率」只对上一半(用户 2026-08-03 拍板补全)。
_H4_QUALITY_DV = "structural_completeness"                 # H4 唯一的检验对象


# ---------------- 复合终点 ----------------

def _z(s: pd.Series) -> pd.Series:
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd else s * 0.0


def build_composites(df: pd.DataFrame) -> pd.DataFrame:
    """保真复合 = z(想象匹配)+z(违背取反)+z(逐镜头 mine 比)+z(embedding Δ,若有) 的均值;
    所有权复合 = own_mean(docs/paper/04 §2.2)。缺哪项跳哪项。"""
    df = df.copy()
    # 保真复合(主终点)=「**主观三腿先平均、再与 embedding Δ 各半**」
    # (docs/paper/04 §2.1;用户 2026-08-03 拍板)。此前是四腿等权 → 唯一的客观腿被稀释到
    # 25%、3 条自评腿合占 75%;答辩火力点正是「无外部真人保真锚」,客观腿不能只占四分之一。
    # embedding 缺席(没跑 embed.py,或 pilot 收敛 r<.3 判为降次要)→ 退回主观三腿等权并告警。
    subj, subj_names = [], []
    for col, sign in zip(_FIDELITY_SUBJ_LEGS, (1, -1, 1)):
        if col in df and df[col].notna().any():  # 整列全 NaN 等同缺失(否则悄悄少一条腿)
            subj.append(sign * _z(df[col]))
            subj_names.append(col)
    if subj:
        _warn_uneven_coverage(df, subj_names)
        _warn_missing_legs("fidelity_composite 的主观侧", _FIDELITY_SUBJ_LEGS, subj_names)
        subj_mean = _combine("fidelity_composite 的主观侧", subj, subj_names)
        if _FIDELITY_OBJ_LEG in df and df[_FIDELITY_OBJ_LEG].notna().any():
            fid = 0.5 * subj_mean + 0.5 * _z(df[_FIDELITY_OBJ_LEG])
            lost = int((fid.isna() & subj_mean.notna()).sum())
            if lost:
                # 逐行缺 embedding 的行会变 NaN。绝不用「这些行退回主观均值」补——那等于
                # 让同一个终点里两种不同权重的数混在一起比,正是 _combine 警告的那种偏。
                warnings.warn(f"fidelity_composite:{lost} 行有主观分但缺 {_FIDELITY_OBJ_LEG},"
                              "按各半定义无法构建 → 记 NaN(不退回主观均值,否则同一终点内混两种权重)。"
                              "请补跑 analysis/embed.py,或在冻结文件里改判 embedding 降次要。")
            df["fidelity_composite"] = fid
            print("  保真复合权重:主观三腿 50% + embedding Δ 50%(docs/paper/04 §2.1 各半)")
        else:
            _warn_missing_legs("fidelity_composite", _FIDELITY_LEGS, subj_names)
            print("  保真复合权重:仅主观三腿等权(embedding Δ 缺席,非各半定义,结论须注明)")
            df["fidelity_composite"] = subj_mean
    if "own_mean" in df:
        df["ownership_composite"] = df["own_mean"]
    # H3a 努力再分配(事后返工复合):n_ai_rounds + 手改字符 + t_postgen。计数/时长右偏,
    # 先 log1p 再 z,复合近似对称、可进高斯 LMM(docs/paper/04 §3.1-2:计数→负二项、
    # 时长→log;复合走 log)。单终点 n_ai_rounds 的确证检验仍应负二项——分析计划冻结时锁定。
    # (深度评审 2026-07-19 #10:此前从未构建,招牌图 H3a 的推断缺一半。)
    eff, eff_names = [], []
    for col in _EFFORT_LEGS:
        if col in df and df[col].notna().any():
            eff.append(_z(np.log1p(pd.to_numeric(df[col], errors="coerce").clip(lower=0))))
            eff_names.append(col)
    if eff:
        _warn_missing_legs("effort_composite", _EFFORT_LEGS, eff_names)
        df["effort_composite"] = _combine("effort_composite", eff, eff_names)
    return df


def build_quality(df: pd.DataFrame) -> pd.DataFrame:
    """H4 的质量 DV = **结构完整度**(docs/paper/03 §4 客观评价栈):
    parse_ok + 分镜四字段齐全率 + 15s且3镜达标 —— 文档定义的是**一个复合**(三者均值,
    0-1 比例),不是三个各测各的终点。合成后 SESOI 的单位无歧义(0.10 = 10 个百分点),
    H4 只做一次 TOST,不产生多重性;三个成分本身只作描述性输出。
    v3.structural() 仅在「一镜也没解析出来」时给 field_completeness=NaN,该行 parse_ok=
    shots_ok=0 → 结构完整度记 0(解析全失败就是客观下界),不丢行、不把失败洗成缺失。"""
    df = df.copy()
    miss = [c for c in _H4_LEGS if c not in df]
    if miss:
        warnings.warn(f"缺 {miss},无法构建 H4 的结构完整度复合 {_H4_QUALITY_DV};H4 将无 DV 可测。")
        return df
    legs = pd.concat([pd.to_numeric(df[c], errors="coerce") for c in _H4_LEGS], axis=1)
    df[_H4_QUALITY_DV] = legs.fillna(0.0).mean(axis=1)
    return df


def build_dose(df: pd.DataFrame) -> pd.DataFrame:
    """H5 剂量复合(docs/paper/04 §3.1-4:自填率 + 答题净时 + 1−AI代答率)= 三者 z 的均值。
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
        df.loc[e, "dose_composite"] = _combine("dose_composite", parts, names)
    return df


def _combine(name: str, parts: list[pd.Series], names: list[str]) -> pd.Series:
    """把在场成分平均成复合,并对两种**静默降级**告警(深度评审 T4):
    ① 某条腿零方差 → _z 返回全 0,该腿对复合毫无贡献,复合退化成剩余腿的换算
       (实测:g_custom_rate / g_ai_decided_rate 恒定时
        corr(dose_composite, z(pre_investment)) = 1.000000,「三成分剂量」其实只是时间腿);
    ② 部分行的非空腿数少于同伴 → 这些行的复合建在更少的腿上、与同伴不同尺度,
       被试间比较(H5 的 OLS)会因此有偏。"""
    mat = pd.concat(parts, axis=1)
    mat.columns = names
    flat = [c for c in names if float(mat[c].std(ddof=0)) == 0]
    if flat:
        warnings.warn(f"{name}: 成分 {flat} 零方差,z 后恒为 0、对复合零贡献;"
                      f"复合实际等价于其余 {len(names) - len(flat)} 条腿的线性换算,"
                      "不可按完整定义解读。")
    cnt = mat.notna().sum(axis=1)
    used = cnt[cnt > 0].value_counts().sort_index().to_dict()
    if len(used) > 1:
        warnings.warn(f"{name}: 各行用到的成分数不一致(腿数→行数 {used},应恒为 {len(names)});"
                      "腿少的行与同伴不同尺度,被试间比较有偏,须补齐或分层报告。")
    return mat.mean(axis=1)


def _warn_missing_legs(name: str, full: tuple, present: list[str]) -> None:
    """复合按不足额的成分集构建时告警(全 NaN 的腿覆盖率均匀为 0,_warn_uneven_coverage 抓不到,
    复合会静默降为少数腿——深度评审 A5)。"""
    miss = [c for c in full if c not in present]
    if miss:
        warnings.warn(f"{name} 仅由 {len(present)}/{len(full)} 个成分构建,缺 {miss};"
                      "与冻结的复合定义不一致,结果须按实际成分集报告。")


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


_LMM_TERMS = ("condition", "topic", "round_idx", "participant_id")


def fit_lmm(df: pd.DataFrame, dv: str):
    """依次试 _OPTIMIZERS,返回第一个拟合成功的。lbfgs 在本设计上常抛 Singular matrix
    而 powell/bfgs/nm 拟合同一模型无碍(N=36 合成数据 6 个终点里 5 个如此),写死单一
    优化器会让冻结分析计划里的确证分析静默消失。全失败则抛,绝不返回 None。

    去空必须覆盖模型的**全部**项(此前只去 dv/condition):一个 NaN topic 会被 patsy
    悄悄丢行、而 groups 仍是全长,五个优化器一起抛
    `IndexError: index 107 is out of bounds for axis 0 with size 107` —— 把「数据缺一格」
    伪装成「数值不收敛」。丢了多少行必须报出来(深度评审 T3)。
    拟合后报 converged / 被试随机效应方差,避免边界解被当成正常结果(T7)。"""
    import statsmodels.formula.api as smf
    d = df.dropna(subset=[dv, *_LMM_TERMS]).copy()
    dropped = len(df) - len(d)
    if dropped:
        na = {c: int(df[c].isna().sum()) for c in (dv, *_LMM_TERMS) if df[c].isna().any()}
        print(f"  注:{dv} 按模型项去空丢了 {dropped}/{len(df)} 行(各项空值数 {na})——"
              "这是**数据缺失**,不是优化器/数值问题。")
    d["order"] = d["round_idx"].astype(float)
    formula = f"Q('{dv}') ~ C(condition) + C(topic) + order"
    errs = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # 5 次尝试的 Convergence/Singular 噪声;收敛性下面显式查
        for opt in _OPTIMIZERS:
            try:
                fit = smf.mixedlm(formula, d, groups=d["participant_id"]).fit(method=opt)
                _report_fit_health(dv, opt, fit)
                return fit
            except Exception as e:  # noqa: BLE001
                errs.append(f"{opt}={type(e).__name__}: {e}")
    raise RuntimeError(f"{dv}: 全部优化器均失败(" + "; ".join(errs) + ")")


def _report_fit_health(dv: str, opt: str, fit) -> None:
    """非收敛 / 随机效应方差压在 0 边界时出声(warnings 被 simplefilter 吞了,不查就没人知道)。"""
    re_var = float(np.ravel(fit.cov_re)[0]) if np.size(fit.cov_re) else float("nan")
    converged = bool(getattr(fit, "converged", True))
    boundary = re_var < 1e-6
    if not converged or boundary:
        why = ("(≈0 = 边界解,(1|被试) 实质没起作用,配对设计的 SE 可能偏乐观)" if boundary
               else "(优化器未收敛,点估计与 SE 都不可信)")  # 别把未收敛也说成边界解
        print(f"  ⚠️ LMM 数值健康({dv},优化器 {opt}):converged={converged}、"
              f"被试随机效应方差={re_var:.3g}{why};"
              "结论须附注,并用 Wilcoxon 配对/OLS 复核。")


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
    **bound 必须是采数前冻结的 a priori SESOI**(dv 原始/z 单位的绝对值),与观测数据无关;
    默认取 prereg.SESOI(单一真源)。切勿用样本自身配对差 SD 现算 bound——那会让等价界
    随抽样噪声漂移、Type I 失控(代码审查 + 统计专家一致指正)。
    SESOI 无效(None / 非正 / 非有限)时**拒绝执行**,不退回任何默认值。

    ⚠️ 已知局限(勿再删):方法学上**每个终点都该按其源量表/文献各设一个 bound**,
    而 prereg.SESOI 现在是**单一标量**,谁调用就套给谁。它是按 H4 的结构完整度
    (0-1 比例,0.10 = 10 个百分点)定的;若把本函数用到别的终点、尤其是 z 单位的复合上,
    这个界的实质含义就变了,必须在冻结文件里为该终点另设界并在报告里写明。"""
    if bound is None:
        bound = prereg.SESOI
    # NaN/inf 也必须走这条:`nan <= 0` 是 False,漏进去会算出 p=nan、equivalent=False
    # ——一个笔误的 SESOI 换来一个看着像结论的「不等价」。
    if bound is None or not np.isfinite(bound) or bound <= 0:
        why = ("prereg.SESOI 仍为 None" if bound is None
               else f"SESOI={bound} 不是正的有限数(等价界须为正的绝对值,疑似笔误/符号写反)")
        print("!" * 78)
        print(f"⛔ TOST 拒绝执行(H4 非劣,dv={dv},{pair[0]}−{pair[1]}):{why}。")
        print(f"   采数前研究员必须拍板:{dv} 上多大的 {pair[0]}−{pair[1]} 差异才算「实质劣于」")
        print("   (即等价界 SESOI,用 DV 原始单位:结构完整度是 0-1 比例,0.10 = 10 个百分点),")
        print("   并把该数值写入 analysis/prereg.py 的 SESOI。在此之前 H4 无结论,不得用默认界代替。")
        print("!" * 78)
        return {"pair": f"{pair[0]}-{pair[1]}", "dv": dv, "equivalent": None,
                "note": f"SESOI 无效({why}),拒绝执行"}
    wide = _paired(df, dv)
    a, b = pair
    if a not in wide or b not in wide:  # 该条件整列缺/全 NaN(试测期 E 未跑、解析全失败)
        return {"pair": f"{a}-{b}", "dv": dv, "equivalent": None, "note": f"缺条件 {a}/{b} 的配对数据"}
    d = (wide[a] - wide[b]).dropna()
    n = len(d)
    if n < 5:
        return {"pair": f"{a}-{b}", "n": n, "equivalent": None}
    m, sd = d.mean(), d.std(ddof=1)
    if sd == 0:  # 零方差 → 除零;t 无定义,非劣无法判定(SESOI 无效已在上面大声拦下)
        return {"pair": f"{a}-{b}", "n": n, "mean_diff": float(m),
                "equivalent": None, "note": "配对差零方差"}
    se = sd / np.sqrt(n)
    p_lower = stats.t.sf((m + bound) / se, n - 1)   # H1: 差 > −bound
    p_upper = stats.t.cdf((m - bound) / se, n - 1)  # H1: 差 < +bound
    return {"pair": f"{a}-{b}", "n": n, "mean_diff": float(m), "bound": float(bound),
            "p_lower": float(p_lower), "p_upper": float(p_upper),
            "equivalent": bool(p_lower < .05 and p_upper < .05)}


def dose_response(df: pd.DataFrame, dv: str, dose: str = "dose_composite") -> dict:
    """E 内:事前投入 → 保真 / 所有权(被试间 OLS;每被试仅 1 个 E,无法被试内中心化
    ——附注局限)。docs/paper/04 §3.1-4 的 H5 因变量是**两个主复合**,不止保真。
    默认剂量为 build_dose 的三成分复合;dose='pre_investment' 可得只看时间的可比版本。"""
    import statsmodels.formula.api as smf
    if dv not in df or dose not in df:
        return {"note": f"缺列 {[c for c in (dv, dose) if c not in df]}"}
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
        print(f"⛔ LMM 失败 → 冻结分析计划里的确证分析(计划对比 + Holm)缺失!终点 = {dv}")
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
    print("\nTOST 机制自测(E vs C;合成数据上显式给 bound=0.5,不是冻结的 SESOI):",
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

    df = build_quality(build_dose(build_composites(pd.read_csv(args.csv))))
    failed = {}
    for dv in prereg.PRIMARY_ENDPOINTS + prereg.SECONDARY_ENDPOINTS:  # 终点层级单一真源
        err = analyze_endpoint(df, dv)
        if err:
            failed[dv] = err

    print("\n########## H4 质量非劣(TOST,E vs D)##########")
    print(f"  DV = 结构完整度 {_H4_QUALITY_DV} = mean(parse_ok, 分镜四字段齐全率, 15s且3镜达标),")
    print("       docs/paper/03 §4 定义的**单一**复合(0-1 比例,与 SESOI 同单位)→ 只做一次检验,无多重性。")
    if _H4_QUALITY_DV in df:
        print(f"  {tost(df, _H4_QUALITY_DV, ('E', 'D'))}")
    else:
        print(f"  ⛔ {_H4_QUALITY_DV} 未构建(CSV 缺 {list(_H4_LEGS)} 里的列),H4 无 DV 可测。")
    print("  ——以下三个成分仅**描述性**,不是 H4 的检验对象(勿单独下非劣结论):")
    print(df.groupby("condition")[[c for c in _H4_LEGS if c in df]].mean().round(3).to_string())

    print("\n########## H5 剂量-反应(E 内,被试间 OLS;事前声明的确认性次分析)##########")
    for dv in prereg.PRIMARY_ENDPOINTS:  # docs/paper/04 §3.1-4:因变量是保真**和**所有权
        print(f"  {dv} ~ 复合剂量(自填率+答题净时+1−AI代答率):", dose_response(df, dv))
        print(f"  {dv} ~ 仅时间(pre_investment,可比参照):",
              dose_response(df, dv, dose="pre_investment"))

    if failed:
        print("\n" + "!" * 78)
        print(f"⛔ 最终警告:{len(failed)} 个冻结分析计划里的终点没有确证结果(LMM+计划对比+Holm 缺失):")
        for dv, err in failed.items():
            print(f"   - {dv}  ←  {err}")
        print("   这些终点当前只有描述性/稳健性输出,不可写进确证性结论。")
        print("!" * 78)
    print("\n注:embedding 相对基线保真 Δ 由 embed.py 合入后进保真复合;质量走 TOST 非劣。")


if __name__ == "__main__":
    main()
