"""研究员后台的「数据分析」面板 —— 把 analysis/ 管线包成按钮,点一下出结果/图,
不用命令行(命令行版仍在 `make ...`,两者共用同一批函数)。仅研究员可见。"""
from __future__ import annotations

import contextlib
import io
import sys
import warnings
from pathlib import Path

import streamlit as st

from core import db
from i18n import t

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = ROOT / "data" / "analysis"
FIGDIR = ANALYSIS_DIR / "figures"


def _capture(fn) -> str:
    """跑一个会 print 的函数,把输出收集成字符串在网页上显示。"""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            fn()
    except Exception as e:  # noqa: BLE001
        buf.write("\n" + t("analysis.captured_err", err=f"{type(e).__name__}: {e}"))
    return buf.getvalue() or t("analysis.no_output")


def _warned(fn):
    """跑一个可能 warnings.warn 的函数,返回 (结果, 警告文本表)。
    面板必须把警告显示到页面上:_capture 只收 stdout,而「保真复合缺 embedding 腿」
    这类致命提示是 warning,过去全被吞掉(研究员只用网页,看不到 stderr)。"""
    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")
        out = fn()
    return out, [str(w.message) for w in ws]


# 由**别的**步骤合进 v3_per_trial.csv 的列(v3.per_trial 自己算不出来):
# embed.py 的 embedding 保真Δ、events.py 的事件层、judge.py 的盲评保真。
# 面板每次点「出分析结果」都重算 per_trial 并覆盖同一个 CSV,不接回来就会**静默抹掉**它们。
_MERGED_ELSEWHERE = ("embed_fidelity", "judge_fidelity",
                     "t_questionnaire", "n_llm_calls", "n_llm_errors",
                     "llm_total_tokens", "llm_wait_max", "n_resumes")


def preserve_merged(pt, csv_path: Path):
    """重算 per-trial 覆盖 CSV 前,把别的步骤合入的列按 (participant_id, round_idx) 接回来。
    返回 (新表, 接回来的列名);接不回来就等于点一次按钮抹掉一次,且毫无提示
    —— embed/events/judge 只把结果存在这个 CSV 里。"""
    if not csv_path.exists():
        return pt, []
    import pandas as pd
    old = pd.read_csv(csv_path)
    keys = ["participant_id", "round_idx"]
    if not set(keys) <= set(old.columns):
        return pt, []
    cols = [c for c in _MERGED_ELSEWHERE if c in old.columns and c not in pt.columns]
    if not cols:
        return pt, []
    return pt.merge(old[keys + cols], on=keys, how="left"), cols


def preserve_embed(pt, csv_path: Path):
    """向后兼容的窄版(只接 embed_fidelity);新代码用 preserve_merged。"""
    return preserve_merged(pt, csv_path)[0]


def render() -> None:
    st.divider()
    st.subheader(t("analysis.title"))
    st.caption(t("analysis.caption"))

    # 惰性导入:只有打开这个面板才加载 pandas/statsmodels/matplotlib
    from analysis import figures as A_fig
    from analysis import pilot_check
    from analysis import power_sim
    from analysis import stats as A_stats
    from analysis import v3

    dbp = db.DB_PATH
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    c1, c2 = st.columns(2)

    # ① 试测体检
    if c1.button(t("analysis.btn_pilot"), width="stretch"):
        st.code(_capture(lambda: pilot_check.run(dbp)), language="text")

    # ④ 功效(慢,几十秒)
    if c2.button(t("analysis.btn_power"), width="stretch"):
        with st.spinner(t("analysis.power_spinner")):
            st.code(_capture(lambda: _run_power(power_sim)), language="text")

    # ② 分析结果:指标 + 统计
    if st.button(t("analysis.btn_results"), type="primary", width="stretch"):
        try:
            df = v3.load(dbp)
            csv_path = ANALYSIS_DIR / "v3_per_trial.csv"
            pt, kept = preserve_merged(v3.per_trial(df), csv_path)
            pt.to_csv(csv_path, index=False)
            if kept:
                st.caption("· ".join(["接回既有列:"] + kept))
            have = [x for x in v3._SUMMARY_COLS if x in pt.columns]
            st.markdown(t("analysis.means_title"))
            st.dataframe(pt.groupby("condition")[have].mean(numeric_only=True).T)
            st.markdown(t("analysis.diversity_title"))
            div = v3.diversity_by_group(df)
            st.dataframe(div if len(div) else None)
            st.markdown(t("analysis.stats_title"))
            comp, warns = _warned(lambda: A_stats.build_composites(pt))
            for w in warns:  # 例:保真复合缺 embed_fidelity 这条腿
                st.warning(f"⚠️ {w}")
            for dv in ("ownership_composite", "fidelity_composite", "satisfaction",
                       "post_investment", "total_investment", "effort_composite"):
                if dv in comp and comp[dv].notna().sum() >= 6:
                    st.code(_capture(lambda dv=dv: A_stats.analyze_endpoint(comp, dv)),
                            language="text")
        except Exception as e:  # noqa: BLE001
            st.warning(t("analysis.err_data", err=f"{type(e).__name__}: {e}"))

    # ③ 出图
    if st.button(t("analysis.btn_figures"), width="stretch"):
        try:
            df = v3.load(dbp)
            pt = v3.per_trial(df)
            comp = A_stats.build_composites(pt)
            FIGDIR.mkdir(parents=True, exist_ok=True)
            A_fig.fig_effort(pt, FIGDIR / "fig_effort.png")
            st.image(str(FIGDIR / "fig_effort.png"), caption=t("analysis.cap_effort"))
            if "ownership_composite" in comp:
                A_fig.fig_dv(comp, "ownership_composite", FIGDIR / "fig_ownership.png")
                st.image(str(FIGDIR / "fig_ownership.png"), caption=t("analysis.cap_ownership"))
        except Exception as e:  # noqa: BLE001
            st.warning(t("analysis.err_figures", err=f"{type(e).__name__}: {e}"))

    st.caption(t("analysis.note"))


def _run_power(power_sim) -> None:
    old = sys.argv
    sys.argv = ["power_sim", "--nsims", "500"]
    try:
        power_sim.main()
    finally:
        sys.argv = old
