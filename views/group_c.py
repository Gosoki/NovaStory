from __future__ import annotations

import streamlit as st

from core import prompts, state
from views._streaming import stream_llm
from views._trial import render_final_and_confirm


def render_pipeline(topic: dict) -> None:
    """Condition C — fully automatic: intent → final script in one shot."""
    if not st.session_state["r_final"]:
        system = prompts.build_system_script(topic)
        user = prompts.build_user(topic, st.session_state["r_intent"])
        out = stream_llm(system, user, group="C-final")
        if out is not None:
            st.session_state["r_final"] = out
            state.log_event("final_shown")
            st.rerun()
        return

    render_final_and_confirm(st.session_state["r_final"])
