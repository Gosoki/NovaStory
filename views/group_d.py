from __future__ import annotations

import streamlit as st

from core import prompts, state, storage
from i18n import t
from views._streaming import stream_llm


def render() -> None:
    state.ensure_start_ts("D")

    topic = st.session_state["topic"]
    locked_seed = st.session_state["a_seed"].strip()

    st.header(t("group_d.title"))
    st.info(t("group_d.instructions", shot_count=topic["shot_count"]))

    st.text_input(t("group_d.seed_label"), value=locked_seed, disabled=True)

    # Step 1: outline
    if st.button(t("group_d.gen_outline"), type="secondary", width="stretch"):
        _gen_outline(topic, locked_seed)

    if st.session_state["d_outline_ai"].strip():
        # initialise user-editable copy once
        if not st.session_state["d_outline_user"].strip():
            st.session_state["d_outline_user"] = st.session_state["d_outline_ai"]

        st.subheader(t("group_d.outline_label"))
        st.caption(t("group_d.outline_hint"))
        st.text_area(
            label=t("group_d.outline_label"),
            key="d_outline_user",
            height=180,
            label_visibility="collapsed",
        )

        # Step 2: final
        if st.button(
            t("group_d.gen_final"),
            type="secondary",
            width="stretch",
        ):
            outline = st.session_state["d_outline_user"].strip()
            if not outline:
                st.error(t("errors.outline_empty"))
            else:
                _gen_final(topic, locked_seed, outline)

    has_final = bool(st.session_state["d_final"].strip())
    if has_final:
        st.subheader(t("group_d.final_label"))
        st.markdown(st.session_state["d_final"])

    if st.button(
        t("group_d.submit"),
        type="primary",
        disabled=not has_final,
        width="stretch",
    ):
        storage.append_row(
            user_id=st.session_state["subject_id"].strip(),
            topic=topic,
            group="D",
            total_time_seconds=state.elapsed_seconds("D"),
            initial_input=locked_seed,
            interventions={
                "ai_outline": st.session_state["d_outline_ai"].strip(),
                "user_edited": st.session_state["d_outline_user"].strip(),
            },
            final_output=st.session_state["d_final"].strip(),
        )
        state.mark_submitted("D")
        st.rerun()


def _gen_outline(topic: dict, seed: str) -> None:
    system = prompts.build_system_outline(topic)
    user = prompts.build_user(topic, seed)
    out = stream_llm(system, user, group="D-outline")
    if out is not None:
        st.session_state["d_outline_ai"] = out
        st.session_state["d_outline_user"] = out  # seed editor
        st.session_state["d_final"] = ""  # invalidate stale final
        st.rerun()


def _gen_final(topic: dict, seed: str, edited_outline: str) -> None:
    system = prompts.build_system_script(topic)
    user = prompts.build_user(topic, seed, base_outline=edited_outline)
    out = stream_llm(system, user, group="D-final")
    if out is not None:
        st.session_state["d_final"] = out
        st.rerun()
