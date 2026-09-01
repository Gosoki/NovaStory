from __future__ import annotations

import json

import streamlit as st

from core import config, db, state
from i18n import get_lang, t
from views import _scale

# Whole-study questionnaire shown once, after all rounds are finished and before
# the completion code. Captures cross-condition preference + behavioral intent +
# overall satisfaction (paper/10 H7). Rounds are referred to by number + topic
# title (a neutral memory aid); analysis maps round_idx → condition via trials.


def render() -> None:
    pid = st.session_state["participant_id"]
    # once=True: the page re-renders on every keystroke, we want one arrival
    # stamp so final_survey_shown → final_survey_submit is its answering time.
    state.log_event("final_survey_shown", once=True)
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
    sat = _scale.likert(t("final_survey.q_overall"), "_fs_sat", anchors="satisfied")
    comment = st.text_area(t("final_survey.comment"), key="_fs_comment")

    if st.button(t("final_survey.submit"), type="primary", width="stretch"):
        # Name the unanswered items back, same as every other form in the study.
        missing = [_scale.short(lbl) for lbl, v in (
            (t("final_survey.q_pref"), pref_sel),
            (t("final_survey.q_reuse"), reuse_sel),
            (t("final_survey.q_overall"), sat),
        ) if v is None]
        if missing:
            st.error(t("errors.unanswered", items=" / ".join(missing)))
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
