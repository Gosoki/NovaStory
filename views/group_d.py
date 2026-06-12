from __future__ import annotations

import streamlit as st

from core import prompts, state
from i18n import t
from views._streaming import stream_llm
from views._trial import render_final_and_confirm

# Streamlit wipes widget-bound keys when the widget unmounts, so the editable
# outline lives in the ephemeral "_outline_edit" widget key and is snapshotted
# into the canonical non-widget key "r_outline_user" on every transition.


def render_pipeline(topic: dict) -> None:
    """Condition D — outline checkpoint: intent → AI outline → human edit → final."""
    intent = st.session_state["r_intent"]

    # Step 1: outline (auto-generated on first entry)
    if not st.session_state["r_outline_ai"]:
        system = prompts.build_system_outline(topic)
        user = prompts.build_user(topic, intent)
        out = stream_llm(system, user, group="D-outline")
        if out is not None:
            st.session_state["r_outline_ai"] = out
            st.session_state["r_outline_user"] = out
            state.log_event("outline_shown")
            st.rerun()
        return

    # Step 2: human edit → final
    if not st.session_state["r_final"]:
        outline = render_outline_editor()
        if st.button(t("group_d.gen_final"), type="secondary", width="stretch"):
            if not outline:
                st.error(t("errors.outline_empty"))
                return
            st.session_state["r_outline_user"] = outline
            state.log_event(
                "final_click",
                {"edited_chars": len(outline), "ai_chars": len(st.session_state["r_outline_ai"])},
            )
            gen_final(topic, intent, outline)
        return

    render_final_and_confirm(st.session_state["r_final"])


def render_outline_editor() -> str:
    """Outline textarea backed by an ephemeral widget key; returns trimmed text."""
    if "_outline_edit" not in st.session_state:
        st.session_state["_outline_edit"] = (
            st.session_state["r_outline_user"] or st.session_state["r_outline_ai"]
        )
    st.subheader(t("group_d.outline_label"))
    st.caption(t("group_d.outline_hint"))
    st.text_area(
        label=t("group_d.outline_label"),
        key="_outline_edit",
        height=180,
        label_visibility="collapsed",
    )
    return (st.session_state.get("_outline_edit") or "").strip()


def gen_final(topic: dict, intent: str, edited_outline: str, *, group: str = "D-final") -> None:
    system = prompts.build_system_script(topic)
    user = prompts.build_user(topic, intent, base_outline=edited_outline)
    out = stream_llm(system, user, group=group)
    if out is not None:
        st.session_state["r_final"] = out
        state.log_event("final_shown")
        st.rerun()
