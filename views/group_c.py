from __future__ import annotations

import streamlit as st

from core import prompts, state, storage
from i18n import t
from views._streaming import stream_llm


def render() -> None:
    state.ensure_start_ts("C")

    topic = st.session_state["topic"]
    locked_seed = st.session_state["a_seed"].strip()

    st.header(t("group_c.title"))
    st.info(t("group_c.instructions", shot_count=topic["shot_count"]))

    st.text_input(t("group_c.seed_label"), value=locked_seed, disabled=True)

    has_output = bool(st.session_state["c_output"].strip())
    if st.button(
        t("group_c.generate"),
        type="secondary",
        width="stretch",
    ):
        _call_llm(topic, locked_seed)

    if has_output:
        st.subheader(t("group_c.output_label"))
        st.markdown(st.session_state["c_output"])

    if st.button(
        t("group_c.submit"),
        type="primary",
        disabled=not has_output,
        width="stretch",
    ):
        storage.append_row(
            user_id=st.session_state["subject_id"].strip(),
            topic=topic,
            group="C",
            total_time_seconds=state.elapsed_seconds("C"),
            initial_input=locked_seed,
            final_output=st.session_state["c_output"].strip(),
        )
        state.mark_submitted("C")
        st.rerun()


def _call_llm(topic: dict, seed: str) -> None:
    system = prompts.build_system_script(topic)
    user = prompts.build_user(topic, seed)
    out = stream_llm(system, user, group="C")
    if out is not None:
        st.session_state["c_output"] = out
        st.rerun()
