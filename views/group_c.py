from __future__ import annotations

import streamlit as st

from core import prompts, state
from i18n import get_lang, t
from views._streaming import stream_llm


def render_pipeline(topic: dict) -> None:
    """Condition C — one-shot: intent → full script, view, submit (no loop)."""
    lang = get_lang()
    out = stream_llm(
        prompts.build_system_script(topic, lang),
        prompts.build_user_script(topic, st.session_state["r_intent"], lang),
        group="C-final",
    )
    if out and out.strip():
        state.add_version(out, "ai")
        st.session_state["r_phase"] = "postgen"
        st.rerun()
    # Generation failed/empty: stream_llm already showed the error; offer a
    # retry so the participant is never hard-stuck with no clickable control.
    elif st.button(t("round.retry"), type="primary", width="stretch"):
        st.rerun()
