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


# 自愿留下的联系方式是全库唯一一列直接个人数据。`participants` 又恰好是表选择器的
# 默认项,所以「打开后台 → 点导出」这条最短路径会把邮箱和人口学、筛查、自由留言一起
# 装进 novastory_participants.csv 发出去。靠「导出前记得删掉这一列」的人工纪律扛不住
# 截稿日,代码里去掉它是一行的事。
_HIDDEN_COLS = ("contact_json",)


def _data_browser() -> None:
    table = st.selectbox(t("researcher.table_label"), db.TABLES)
    df = db.load_table(table).drop(columns=list(_HIDDEN_COLS), errors="ignore")
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
