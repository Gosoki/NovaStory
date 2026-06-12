from __future__ import annotations

import streamlit as st

from core import db
from i18n import t


def render() -> None:
    st.header(t("researcher.title"))

    c = db.counts()
    cols = st.columns(4)
    cols[0].metric(t("researcher.n_participants"), c["participants"])
    cols[1].metric(t("researcher.n_passed"), c["passed"])
    cols[2].metric(t("researcher.n_done"), c["done"])
    cols[3].metric(t("researcher.n_trials"), c["trials"])

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
