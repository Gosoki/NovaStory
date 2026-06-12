from __future__ import annotations

import json

import streamlit as st

from core import db, llm, shots, state
from i18n import t


def render_final_and_confirm(final_text: str) -> None:
    """Shared tail of every condition pipeline: show final script, confirm."""
    st.subheader(t("round.final_label"))
    st.markdown(final_text)
    if st.button(t("round.confirm_final"), type="primary", width="stretch"):
        submit_trial(final_text)
        st.rerun()


def submit_trial(final_text: str) -> None:
    rd = state.current_round()
    cond, topic = rd["condition"], rd["topic"]
    state.log_event("trial_submit")
    durs = state.round_durations(cond)
    meta = llm.current_meta()
    parsed = shots.parse_shots(final_text)

    dissent_json = None
    if cond == "E":
        dissent_json = json.dumps(
            {
                "defaults": st.session_state["r_defaults"],
                "dissent": st.session_state["r_dissent"],
                "divergence_dim": st.session_state["divergence_dim"],
            },
            ensure_ascii=False,
        )

    trial_id = db.insert_trial(
        participant_id=st.session_state["participant_id"],
        round_idx=st.session_state["round_idx"],
        condition=cond,
        topic_json=json.dumps(topic, ensure_ascii=False),
        intent_statement=st.session_state["r_intent"],
        ai_outline=st.session_state["r_outline_ai"] or None,
        edited_outline=st.session_state["r_outline_user"] or None,
        dissent_json=dissent_json,
        adjudication=st.session_state["r_adjudication"] or None,
        adjudication_reason=st.session_state["r_adjudication_reason"] or None,
        final_output=final_text,
        parse_ok=int(bool(parsed)),
        regen_count=st.session_state["r_regen"],
        **meta,
        **durs,
    )
    st.session_state["r_trial_id"] = trial_id
    st.session_state["r_phase"] = "questionnaire"
