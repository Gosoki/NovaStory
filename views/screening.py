from __future__ import annotations

import streamlit as st

from core import db, state
from i18n import t

# Indices of the correct quiz options. Nobody is gated on these anymore —
# novice status is recorded as a covariate (screening_json["is_novice"]) and
# subset in analysis (see paper/4 §5.3).
_QUIZ1_CORRECT = 1
_QUIZ2_CORRECT = 1


def render() -> None:
    st.header(t("screening.title"))
    st.caption(t("screening.hint"))

    with st.form("screening_form"):
        st.subheader(t("screening.demo_section"))
        age = st.selectbox(
            t("screening.age"),
            [t(f"screening.age_opt{i}") for i in range(1, 6)],
            index=None,
        )
        gender = st.selectbox(
            t("screening.gender"),
            [t(f"screening.gender_opt{i}") for i in range(1, 5)],
            index=None,
        )
        ai_freq = st.selectbox(
            t("screening.ai_freq"),
            [t(f"screening.ai_freq_opt{i}") for i in range(1, 5)],
            index=None,
        )

        st.subheader(t("screening.exp_section"))
        published = st.radio(
            t("screening.published"),
            [t(f"screening.published_opt{i}") for i in range(1, 4)],
            index=None,
        )
        background = st.radio(
            t("screening.background"),
            [t("screening.yes"), t("screening.no")],
            index=None,
            horizontal=True,
        )
        written = st.radio(
            t("screening.written"),
            [t("screening.yes"), t("screening.no")],
            index=None,
            horizontal=True,
        )
        self_rating = st.segmented_control(
            t("screening.self_rating"),
            list(range(1, 8)),
            selection_mode="single",
            key="_scr_self",
        )

        st.subheader(t("screening.quiz_section"))
        quiz1 = st.radio(
            t("screening.quiz1"),
            [t(f"screening.quiz1_opt{i}") for i in range(1, 5)],
            index=None,
        )
        quiz2 = st.radio(
            t("screening.quiz2"),
            [t(f"screening.quiz2_opt{i}") for i in range(1, 5)],
            index=None,
        )

        submitted = st.form_submit_button(
            t("screening.submit"), type="primary", width="stretch"
        )

    if not submitted:
        return

    answers = [age, gender, ai_freq, published, background, written, self_rating, quiz1, quiz2]
    if any(a is None for a in answers):
        st.error(t("errors.answer_all"))
        return

    quiz1_idx = [t(f"screening.quiz1_opt{i}") for i in range(1, 5)].index(quiz1)
    quiz2_idx = [t(f"screening.quiz2_opt{i}") for i in range(1, 5)].index(quiz2)
    published_idx = [t(f"screening.published_opt{i}") for i in range(1, 4)].index(published)
    quiz_correct = int(quiz1_idx == _QUIZ1_CORRECT) + int(quiz2_idx == _QUIZ2_CORRECT)
    is_no = t("screening.no")

    # Recorded, not gating: everyone proceeds to the experiment.
    is_novice = (
        published_idx == 0
        and background == is_no
        and written == is_no
        and int(self_rating) <= 2
        and quiz_correct <= 1
    )

    demographics = {"age": age, "gender": gender, "ai_freq": ai_freq}
    screening = {
        "published": published,
        "background": "no" if background == is_no else "yes",
        "written": "no" if written == is_no else "yes",
        "self_rating": int(self_rating),
        "quiz1_idx": quiz1_idx,
        "quiz2_idx": quiz2_idx,
        "quiz_correct": quiz_correct,
        "is_novice": is_novice,
    }
    pid, seq = db.insert_participant(
        st.session_state.get("lang", "zh"), demographics, screening, passed=True
    )
    state.begin_rounds(pid, seq)
    st.rerun()
