from __future__ import annotations

import streamlit as st

from analysis import prereg
from core import config, db, state
from i18n import DEFAULT_LANG, t
from views import _scale, _scroll, consent

# Indices of the correct quiz options. Nobody is gated on these anymore —
# novice status is recorded as a covariate (screening_json["is_novice"]) and
# subset in analysis (see paper/4 §5.3).
_QUIZ1_CORRECT = 1
_QUIZ2_CORRECT = 1


def render() -> None:
    state.log_intake_event("screening_shown")
    st.header(t("screening.title"))
    st.caption(t("screening.hint"))

    with st.form("screening_form"):
        st.subheader(t("screening.demo_section"))
        age = st.selectbox(
            t("screening.age"),
            [t(f"screening.age_opt{i}") for i in range(1, 7)],
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
        # AI *creative* experience — distinct from ai_freq (general usage); a
        # covariate for D/E workflow familiarity (paper/10 H1-H3, H7).
        aiexp = st.selectbox(
            t("screening.aiexp"),
            [t(f"screening.aiexp_opt{i}") for i in range(1, 4)],
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
        self_rating = _scale.likert(
            t("screening.self_rating"), "_scr_self", anchors="skill"
        )
        # 自我效能:「我有把握写出来」。与 self_rating(自评熟练度)是两回事 ——
        # 熟练度问的是既有技能,把握问的是**面对这个任务时的信心**,后者才是
        # 所有权/主导感这类结果变量的经典调节量。和其它基线特质一样在**前测**,
        # 免得被体验本身污染。不进 novice 定义(那是冻结的)。
        script_conf = _scale.likert(
            t("screening.script_confidence"), "_scr_conf"
        )

        # Baseline traits (7-point), measured pre-task so they aren't contaminated
        # by the experience — covariates/moderators for paper/10 H1 (ownership)
        # and H7 (reverse branch: trusting novices may be satisfied with C/D).
        st.subheader(t("screening.trait_section"))
        trust = _scale.likert(t("screening.trust"), "_scr_trust")
        own_trait = _scale.likert(t("screening.own_trait"), "_scr_own")

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

    # (label, value) so an unanswered item can be named back to the subject —
    # same feedback as the per-round questionnaire (a 13-item form must not say
    # only "something is missing" and leave them hunting for it).
    items = [
        (t("screening.age"), age), (t("screening.gender"), gender),
        (t("screening.ai_freq"), ai_freq), (t("screening.aiexp"), aiexp),
        (t("screening.published"), published), (t("screening.background"), background),
        (t("screening.written"), written), (t("screening.self_rating"), self_rating),
        (t("screening.script_confidence"), script_conf),
        (t("screening.trust"), trust), (t("screening.own_trait"), own_trait),
        (t("screening.quiz1"), quiz1), (t("screening.quiz2"), quiz2),
    ]
    missing = [_scale.truncate_label(lbl) for lbl, v in items if v is None]
    if missing:
        st.error(t("errors.unanswered", items=" / ".join(missing)))
        return

    # Store categorical answers as language-invariant option indices (not the
    # localized display string), so ja-subject and zh-researcher rows align in
    # analysis. `lang` is on the participant row to recover labels if needed.
    age_idx = [t(f"screening.age_opt{i}") for i in range(1, 7)].index(age)
    gender_idx = [t(f"screening.gender_opt{i}") for i in range(1, 5)].index(gender)
    ai_freq_idx = [t(f"screening.ai_freq_opt{i}") for i in range(1, 5)].index(ai_freq)
    aiexp_idx = [t(f"screening.aiexp_opt{i}") for i in range(1, 4)].index(aiexp)
    quiz1_idx = [t(f"screening.quiz1_opt{i}") for i in range(1, 5)].index(quiz1)
    quiz2_idx = [t(f"screening.quiz2_opt{i}") for i in range(1, 5)].index(quiz2)
    published_idx = [t(f"screening.published_opt{i}") for i in range(1, 4)].index(published)
    quiz_correct = int(quiz1_idx == _QUIZ1_CORRECT) + int(quiz2_idx == _QUIZ2_CORRECT)
    is_no = t("screening.no")

    demographics = {"age_idx": age_idx, "gender_idx": gender_idx, "ai_freq_idx": ai_freq_idx}
    screening = {
        # 同意书版本存证:指纹在同意页勾选那一刻抓(views/consent.py),这里只落库;
        # 没抓到(测试脚本直接从筛查页进)才现算。
        **(st.session_state.get("_consent_proof") or consent.proof_now()),
        "published_idx": published_idx,
        "background": "no" if background == is_no else "yes",
        "written": "no" if written == is_no else "yes",
        "self_rating": int(self_rating),
        "script_confidence": int(script_conf),
        "aiexp_idx": aiexp_idx,
        "trust": int(trust),
        "own_trait": int(own_trait),
        "quiz1_idx": quiz1_idx,
        "quiz2_idx": quiz2_idx,
        "quiz_correct": quiz_correct,
    }
    # Recorded, not gating: everyone proceeds to the experiment.
    # 判定走 analysis.prereg.is_novice(容忍多余键,直接喂整份 screening)—— 这里曾经有一份
    # 内联的同义逻辑,与 prereg 的 NOVICE_DEF 字符串并存(2026-09-01 拍板 2.1)。入库的这个
    # 布尔只是**便利列**,分析侧一律从原始 5 项重算。
    screening["is_novice"] = prereg.is_novice(screening)
    # 题库必须先于入库就绪:`enter_intro` 在题数不足时抛 RuntimeError,而那一刻被试行
    # 已经 INSERT、拉丁方 seq 已被消耗 —— 留下一个永远走不完的孤儿被试,还挪动了后续
    # 所有人的轮转。deploy_check 在部署前拦一次;这里兜住「采数期间有人改坏
    # topics.json」的运行时窗口。
    if len(state.load_topics()) < config.N_ROUNDS:
        st.error(t("errors.not_ready"))
        return

    state.log_intake_event("screening_submit")
    pid, seq, token = db.insert_participant(
        st.session_state.get("lang", DEFAULT_LANG), demographics, screening, passed=True
    )
    # Now that the id exists, claim this browser session's intake events for it.
    db.attach_intake_events(pid, st.session_state.get("session_id", ""))
    # → the how-it-works briefing; the round clock starts when they leave it.
    state.enter_intro(pid, seq, token)
    _scroll.request()   # 筛查 → 说明页:整页换内容
    st.rerun()
