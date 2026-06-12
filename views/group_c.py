from __future__ import annotations

import streamlit as st

from core import prompts, state
from views._streaming import stream_llm


def render_pipeline(topic: dict) -> None:
    """Condition C — one-shot: intent → full script, view, submit (no loop)."""
    out = stream_llm(
        prompts.build_system_script(topic),
        prompts.build_user_script(topic, st.session_state["r_intent"]),
        group="C-final",
    )
    if out is not None:
        state.add_version(out, "ai")
        st.session_state["r_phase"] = "postgen"
        st.rerun()
