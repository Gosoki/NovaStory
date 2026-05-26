from __future__ import annotations

import json

import streamlit as st

from core import storage
from i18n import t


def render() -> None:
    st.header(t("researcher.title"))
    df = storage.load_df()
    if df.empty:
        st.info(t("researcher.empty"))
        return

    # Friendlier display: extract topic title from JSON for filtering
    def topic_title(s: str) -> str:
        try:
            return json.loads(s).get("title", s)
        except (json.JSONDecodeError, AttributeError):
            return s or ""

    df = df.copy()
    df["Topic_Title"] = df["Topic"].apply(topic_title)

    col1, col2 = st.columns(2)
    with col1:
        user_options = ["(all)"] + sorted(df["User_ID"].unique().tolist())
        user_pick = st.selectbox(t("researcher.filter_user"), user_options)
    with col2:
        topic_options = ["(all)"] + sorted(df["Topic_Title"].unique().tolist())
        topic_pick = st.selectbox(t("researcher.filter_topic"), topic_options)

    filtered = df
    if user_pick != "(all)":
        filtered = filtered[filtered["User_ID"] == user_pick]
    if topic_pick != "(all)":
        filtered = filtered[filtered["Topic_Title"] == topic_pick]

    st.caption(t("researcher.row_count", n=len(filtered)))

    st.subheader(t("researcher.group_count"))
    st.bar_chart(filtered["Group"].value_counts())

    st.dataframe(
        filtered.drop(columns=["Topic_Title"]),
        width="stretch",
        hide_index=True,
    )

    st.download_button(
        label=t("researcher.download"),
        data=storage.download_bytes(),
        file_name="experiment_results.csv",
        mime="text/csv",
        width="stretch",
    )
