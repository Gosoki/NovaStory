from __future__ import annotations

import json

import streamlit as st

from core import db, llm, shots, state


def submit_trial(final_text: str) -> None:
    rd = state.current_round()
    cond, topic = rd["condition"], rd["topic"]
    state.log_event("trial_submit")
    durs = state.round_durations(cond)
    meta = llm.current_meta()
    parsed = shots.parse_shots(final_text)

    guidance_json = None
    if cond == "E" and st.session_state["r_guidance_rounds"]:
        guidance_json = json.dumps(
            {"rounds": st.session_state["r_guidance_rounds"]}, ensure_ascii=False
        )
    revision_requests = None
    if cond == "D" and st.session_state["r_revision_requests"]:
        revision_requests = json.dumps(
            st.session_state["r_revision_requests"], ensure_ascii=False
        )

    trial_id = db.insert_trial(
        participant_id=st.session_state["participant_id"],
        round_idx=st.session_state["round_idx"],
        condition=cond,
        topic_json=json.dumps(topic, ensure_ascii=False),
        intent_statement=st.session_state["r_intent"],
        final_output=final_text,
        parse_ok=int(bool(parsed)),
        guidance_json=guidance_json,
        revision_requests=revision_requests,
        script_versions=json.dumps(st.session_state["r_versions"], ensure_ascii=False),
        n_ai_rounds=st.session_state["r_n_ai_rounds"],
        n_hand_edits=st.session_state["r_n_hand_edits"],
        hand_edit_chars=st.session_state["r_hand_edit_chars"],
        **meta,
        **durs,
    )
    st.session_state["r_trial_id"] = trial_id
    st.session_state["r_phase"] = "questionnaire"
