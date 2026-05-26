from __future__ import annotations

import streamlit as st

from core import prompts, state, storage
from i18n import t
from views._streaming import stream_llm


def render() -> None:
    state.ensure_start_ts("B")

    topic = st.session_state["topic"]
    st.header(t("group_b.title"))
    st.info(t("group_b.instructions", shot_count=topic["shot_count"]))

    st.text_input(t("group_b.seed_label"), key="b_seed")
    st.text_area(
        t("group_b.prompt_label"),
        key="b_prompt",
        placeholder=t("group_b.prompt_placeholder"),
        height=110,
    )

    has_output = bool(st.session_state["b_output"].strip())
    label = t("group_b.regenerate") if has_output else t("group_b.generate")
    if st.button(label, type="secondary", width="stretch"):
        seed = st.session_state["b_seed"].strip()
        if not seed:
            st.error(t("errors.seed_empty"))
        else:
            _call_llm(topic, seed, st.session_state["b_prompt"].strip())

    if has_output:
        st.subheader(t("group_b.output_label"))
        st.markdown(st.session_state["b_output"])

    ready = has_output and bool(st.session_state["b_seed"].strip())
    if st.button(
        t("group_b.submit"),
        type="primary",
        disabled=not ready,
        width="stretch",
    ):
        storage.append_row(
            user_id=st.session_state["subject_id"].strip(),
            topic=topic,
            group="B",
            total_time_seconds=state.elapsed_seconds("B"),
            initial_input=st.session_state["b_seed"].strip(),
            final_output=st.session_state["b_output"].strip(),
        )
        state.mark_submitted("B")
        st.rerun()


def _call_llm(topic: dict, seed: str, extra: str) -> None:
    system = prompts.build_system_script(topic)
    user = prompts.build_user(topic, seed, extra=extra or None)
    out = stream_llm(system, user, group="B")
    if out is not None:
        st.session_state["b_output"] = out
        st.rerun()
