from __future__ import annotations

import streamlit as st

from core import db
from i18n import t
from views import analysis_panel, monitor_panel


def render() -> None:
    st.header(t("researcher.title"))
    monitor_panel.render()      # 概览卡片 + 采数进度/平衡统计图
    st.divider()
    _data_browser()             # 四表原始浏览 + CSV 导出
    analysis_panel.render()     # 📊 数据分析(一键出结果/图)


def _data_browser() -> None:
    table = st.selectbox(t("researcher.table_label"), db.TABLES)
    df = db.load_table(table)
    if df.empty:
        st.info(t("researcher.empty"))
        return

    if "participant_id" in df.columns:
        options = ["(all)"] + sorted(df["participant_id"].unique().tolist())
        pick = st.selectbox(t("researcher.filter_participant"), options)
        if pick != "(all)":
            df = df[df["participant_id"] == pick]

    st.caption(t("researcher.row_count", n=len(df)))
    st.dataframe(df, width="stretch", hide_index=True)

    st.download_button(
        label=t("researcher.download"),
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"novastory_{table}.csv",
        mime="text/csv",
        width="stretch",
    )
