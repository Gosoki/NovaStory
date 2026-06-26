from __future__ import annotations

import json

import streamlit as st

from core import config, db, state
from i18n import get_lang, t

# Whole-study questionnaire shown once, after all rounds are finished and before
# the completion code. Captures cross-condition preference + behavioral intent +
# overall satisfaction (paper/10 H7). Rounds are referred to by number + topic
# title (a neutral memory aid); analysis maps round_idx → condition via trials.


def render() -> None:
    pid = st.session_state["participant_id"]
    st.header(t("final_survey.title"))
    st.caption(t("final_survey.hint"))

    plan = st.session_state["round_plan"]
    lang = get_lang()
    rounds = list(range(1, config.N_ROUNDS + 1))

    # Options are the localized label strings (round # + topic title as a neutral
    # memory aid); a format_func radio isn't reliably test-drivable. Map back to
    # the round number on read; analysis maps round_idx → condition via trials.
    labels = [
        f"{t('final_survey.round_n', i=i)}:{state.topic_text(plan[i - 1]['topic'], 'title', lang)}"
        for i in rounds
    ]
    lbl2round = dict(zip(labels, rounds))
    pref_sel = st.radio(t("final_survey.q_pref"), labels, index=None)
    reuse_sel = st.radio(t("final_survey.q_reuse"), labels, index=None)
    st.markdown(t("final_survey.q_overall"))
    sat = st.segmented_control(
        t("final_survey.q_overall"), list(range(1, 8)),
        selection_mode="single", key="_fs_sat", label_visibility="collapsed",
    )
    comment = st.text_area(t("final_survey.comment"), key="_fs_comment")

    if st.button(t("final_survey.submit"), type="primary", width="stretch"):
        if pref_sel is None or reuse_sel is None or sat is None:
            st.error(t("errors.answer_all"))
            return
        db.update_participant(
            pid,
            final_survey_json=json.dumps(
                {
                    "pref_round": lbl2round[pref_sel],
                    "reuse_round": lbl2round[reuse_sel],
                    "overall_sat": int(sat),
                    "comment": (comment or "").strip(),
                },
                ensure_ascii=False,
            ),
        )
        state.log_event("final_survey_submit")
        st.session_state["stage"] = "done"
        st.rerun()
