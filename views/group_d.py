from __future__ import annotations

import streamlit as st

from core import prompts, state
from i18n import get_lang, t
from views._streaming import stream_llm


def render_pipeline(topic: dict) -> None:
    """Condition D — generate-then-repair: same first generation as C, then the
    post-generation loop (free-form revision requests + direct editing)."""
    lang = get_lang()
    out = stream_llm(
        prompts.build_system_script(topic, lang),
        prompts.build_user_script(topic, st.session_state["r_intent"], lang),
        group="D-final",
    )
    if out and out.strip():
        state.add_version(out, "ai")
        st.session_state["r_phase"] = "postgen"
        st.rerun()
    elif st.button(t("round.retry"), type="primary", width="stretch"):
        st.rerun()
