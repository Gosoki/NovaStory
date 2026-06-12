from __future__ import annotations

import streamlit as st

from i18n import t


def render() -> None:
    st.header(t("consent.title"))
    st.markdown(t("consent.body"))
    agree = st.checkbox(t("consent.agree"), key="_consent_agree")
    if st.button(
        t("consent.start"),
        type="primary",
        disabled=not agree,
        width="stretch",
    ):
        st.session_state["stage"] = "screening"
        st.rerun()
