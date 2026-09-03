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


# 永不进表格视图与 CSV 的列(出口黑名单;新增一列直接个人数据或凭据时**必须来这里登记**):
#   contact_json —— 自愿留下的邮箱,全库唯一一列直接个人数据。`participants` 又恰好是
#     表选择器的默认项,「打开后台 → 点导出」这条最短路径会把它和人口学、筛查、自由留言
#     一起装进 novastory_participants.csv 发出去。靠人工纪律扛不住截稿日。
#   token —— `?t=` 续接句柄,只凭它就能恢复一位被试的身份(core/state._attempt_resume)。
#     它是凭据,不是数据;要读请直接开 sqlite。
_NEVER_EXPORTED_COLS = ("contact_json", "token")


def _csv_safe(df):
    """Excel 公式注入:单元格以 = + - @ 或制表/回车开头会被当成公式(被试自由文本里
    「-特になし」这种很常见,会静默变成公式错误)。前面垫一个单引号,Excel 显示为文本。"""
    def fix(v):
        return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v
    return df.map(fix)


def _data_browser() -> None:
    table = st.selectbox(t("researcher.table_label"), db.TABLES)
    df = db.load_table(table).drop(columns=list(_NEVER_EXPORTED_COLS), errors="ignore")
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
        data=_csv_safe(df).to_csv(index=False).encode("utf-8-sig"),
        file_name=f"novastory_{table}.csv",
        mime="text/csv",
        width="stretch",
    )
