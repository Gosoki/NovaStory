from __future__ import annotations

import streamlit as st

from i18n import t
from views._lang import language_radio


def render() -> None:
    # Subject picks their language once, here, before consenting. After this it is
    # not shown to the subject anywhere (only admins can switch it) — keeps a
    # Japanese subject from ever flipping into another language mid-study (JP6).
    language_radio("_consent_lang")
    st.divider()
    st.header(t("consent.title"))
    st.markdown(t("consent.body"))
    st.info(t("consent.overview"))  # how the study works (3 rounds, flow, no right answers)
    st.warning(t("consent.no_refresh"))
    agree = st.checkbox(t("consent.agree"), key="_consent_agree")
    if st.button(
        t("consent.start"),
        type="primary",
        disabled=not agree,
        width="stretch",
    ):
        st.session_state["stage"] = "screening"
        st.rerun()
