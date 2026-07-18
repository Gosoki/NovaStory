"""研究员后台的「数据分析」面板 —— 把 analysis/ 管线包成按钮,点一下出结果/图,
不用命令行(命令行版仍在 `make ...`,两者共用同一批函数)。仅研究员可见。"""
from __future__ import annotations

import contextlib
import io
import sys
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
            pt = v3.per_trial(df)
            pt.to_csv(ANALYSIS_DIR / "v3_per_trial.csv", index=False)
            have = [x for x in v3._SUMMARY_COLS if x in pt.columns]
            st.markdown(t("analysis.means_title"))
            st.dataframe(pt.groupby("condition")[have].mean(numeric_only=True).T)
            st.markdown(t("analysis.diversity_title"))
            div = v3.diversity_by_group(df)
            st.dataframe(div if len(div) else None)
            st.markdown(t("analysis.stats_title"))
            comp = A_stats.build_composites(pt)
            for dv in ("ownership_composite", "fidelity_composite", "satisfaction",
                       "post_investment", "total_investment"):
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
