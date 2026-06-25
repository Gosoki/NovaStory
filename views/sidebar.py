from __future__ import annotations

import json

import streamlit as st

from core import config, state
from i18n import AVAILABLE_LANGS, LANG_LABELS, t
from views import devtools


def render() -> None:
    with st.sidebar:
        _researcher_section()


def _language_picker() -> None:
    st.subheader(t("sidebar.language"))
    st.radio(
        label=t("sidebar.language"),
        options=AVAILABLE_LANGS,
        format_func=lambda code: LANG_LABELS[code],
        horizontal=True,
        label_visibility="collapsed",
        key="lang",
    )


def _researcher_section() -> None:
    with st.expander(t("sidebar.researcher_section"), expanded=False):
        if not st.session_state.get("researcher_ok"):
            pw = st.text_input(
                t("sidebar.researcher_pw"), type="password", key="_researcher_pw"
            )
            if st.button(t("sidebar.researcher_unlock"), width="stretch"):
                if pw == config.researcher_password():
                    st.session_state["researcher_ok"] = True
                    st.rerun()
                else:
                    st.error(t("sidebar.researcher_pw_wrong"))
            return

        st.toggle(t("sidebar.researcher_toggle"), key="researcher_mode")
        # Language picker lives here (researcher-only): participants stay locked
        # to DEFAULTS["lang"]="ja" and can never switch mid-session, which also
        # protects the localized-string answer comparisons (JP6 / AUD4).
        _language_picker()
        st.divider()
        devtools.render()
        _api_section()
        _topics_preview()
        if st.button(t("sidebar.reset_subject"), width="stretch"):
            state.reset_for_next()
            st.rerun()
        if st.button(t("sidebar.researcher_logout"), width="stretch"):
            st.session_state["researcher_ok"] = False
            st.session_state["researcher_mode"] = False
            st.rerun()


def _api_section() -> None:
    st.subheader(t("sidebar.api_section"))
    cfgs = state.load_api_configs()
    if not cfgs:
        st.warning(t("sidebar.api_no_configs"))
        return

    names = [c.get("name", f"#{i}") for i, c in enumerate(cfgs)]
    if "_api_preset_idx" not in st.session_state:
        st.session_state["_api_preset_idx"] = 0
    if st.session_state["_api_preset_idx"] >= len(cfgs):
        st.session_state["_api_preset_idx"] = 0

    idx = st.selectbox(
        t("sidebar.api_preset_label"),
        options=list(range(len(cfgs))),
        format_func=lambda i: names[i],
        key="_api_preset_idx",
    )
    chosen = cfgs[idx]
    # Push into the session keys that core/llm.py reads.
    st.session_state["base_url"] = chosen["base_url"]
    st.session_state["model"] = chosen["model"]
    st.session_state["api_key"] = chosen["api_key"]
    st.session_state["api_preset_name"] = chosen.get("name", "")

    st.caption(t("sidebar.api_active", name=chosen.get("name", "—")))
    if not chosen["api_key"]:
        st.warning(t("sidebar.api_key_missing"))


def _topics_preview() -> None:
    st.subheader(t("sidebar.topic_section"))
    st.caption(t("sidebar.topics_hint"))
    topics = state.load_topics()
    st.json(json.loads(json.dumps(topics, ensure_ascii=False)), expanded=False)
