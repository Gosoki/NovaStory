from __future__ import annotations

import streamlit as st

from core import state, storage
from i18n import t


def render() -> None:
    state.ensure_start_ts("A")
    state.freeze_topic()  # subject has now begun → lock topic edits

    topic = st.session_state["topic"]

    st.header(t("group_a.title"))
    st.info(
        t(
            "group_a.instructions",
            shot_count=topic["shot_count"],
            total_seconds=topic["total_seconds"],
        )
    )

    st.text_area(
        t("group_a.script_label"),
        key="_a_script_input",
        placeholder=t("group_a.script_placeholder"),
        height=260,
    )
    st.text_input(
        t("group_a.seed_label"),
        key="_a_seed_input",
        placeholder=t("group_a.seed_placeholder"),
    )

    script_val = (st.session_state.get("_a_script_input") or "").strip()
    seed_val = (st.session_state.get("_a_seed_input") or "").strip()
    ready = bool(script_val) and bool(seed_val)

    if st.button(
        t("group_a.submit"),
        type="primary",
        disabled=not ready,
        width="stretch",
    ):
        # Persist values to non-widget state keys before the A view unmounts.
        # C/D will read `a_seed` as the locked seed; without this snapshot it disappears.
        st.session_state["a_script"] = script_val
        st.session_state["a_seed"] = seed_val
        # Seed B with A's hook so subject can tweak
        if not st.session_state["b_seed"].strip():
            st.session_state["b_seed"] = seed_val
        storage.append_row(
            user_id=st.session_state["subject_id"].strip(),
            topic=topic,
            group="A",
            total_time_seconds=state.elapsed_seconds("A"),
            initial_input=seed_val,
            final_output=script_val,
        )
        state.mark_submitted("A")
        st.rerun()
