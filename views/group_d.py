from __future__ import annotations

import streamlit as st

from core import prompts, state
from views._streaming import stream_llm


def render_pipeline(topic: dict) -> None:
    """Condition D — generate-then-repair: same first generation as C, then the
    post-generation loop (free-form revision requests + direct editing)."""
    out = stream_llm(
        prompts.build_system_script(topic),
        prompts.build_user_script(topic, st.session_state["r_intent"]),
        group="D-final",
    )
    if out is not None:
        state.add_version(out, "ai")
        st.session_state["r_phase"] = "postgen"
        st.rerun()
