from __future__ import annotations

import json

import streamlit as st

from core import db, shots, state
from i18n import t

_SCALE = list(range(1, 8))
_OWN_ITEMS = 3          # q.own1..own3 (trimmed for session length)
_SOA_ITEMS = 2          # q.soa1..soa2
_TLX_ITEMS = 1          # q.tlx1
_ATTENTION_ROUND = 2    # attention check embedded in round 2's questionnaire
_ATTENTION_EXPECTED = 2
_SHOT_TAGS = ("mine", "ai_ok", "ai_against")


def render() -> None:
    ridx = st.session_state["round_idx"]
    st.subheader(t("q.title"))
    st.caption(t("q.hint"))

    answers: dict[str, object] = {}
    missing = False

    def likert(key: str, label: str) -> None:
        nonlocal missing
        val = st.segmented_control(
            label, _SCALE, selection_mode="single", key=f"_q_{key}_{ridx}"
        )
        if val is None:
            missing = True
        answers[key] = val

    for i in range(1, _OWN_ITEMS + 1):
        likert(f"own{i}", t(f"q.own{i}"))
    for i in range(1, _SOA_ITEMS + 1):
        likert(f"soa{i}", t(f"q.soa{i}"))
    if ridx == _ATTENTION_ROUND:
        likert("attention", t("q.attention"))
    for i in range(1, _TLX_ITEMS + 1):
        likert(f"tlx{i}", t(f"q.tlx{i}"))
    likert("violation", t("q.violation"))
    likert("imagine", t("q.imagine"))

    # ---- per-shot intent annotation ----
    st.subheader(t("q.shots_title"))
    parsed = shots.parse_shots(st.session_state["r_final"])
    shot_annotations: list[dict] = []
    if parsed:
        st.caption(t("q.shots_hint"))
        for s in parsed:
            with st.container(border=True):
                st.markdown(_shot_preview(s))
                tag = st.segmented_control(
                    t("q.shot_tag_label"),
                    _SHOT_TAGS,
                    selection_mode="single",
                    format_func=lambda c: t(f"q.tag_{c}"),
                    key=f"_q_shot{s['idx']}_{ridx}",
                )
                if tag is None:
                    missing = True
                shot_annotations.append({"shot": s["idx"], "tag": tag})
    else:
        tag = st.segmented_control(
            t("q.whole_tag_label"),
            _SHOT_TAGS,
            selection_mode="single",
            format_func=lambda c: t(f"q.tag_{c}"),
            key=f"_q_whole_{ridx}",
        )
        if tag is None:
            missing = True
        shot_annotations.append({"shot": 0, "tag": tag})

    if st.button(t("q.submit"), type="primary", width="stretch"):
        if missing:
            st.error(t("errors.answer_all"))
            return
        _submit(ridx, answers, shot_annotations)
        st.rerun()


def _shot_preview(s: dict) -> str:
    head = f"**{t('q.shot_label', i=s['idx'])}**"
    body = s.get("visual") or s.get("raw", "")
    if len(body) > 120:
        body = body[:120] + "…"
    return f"{head} {body}"


def _submit(ridx: int, answers: dict, shot_annotations: list[dict]) -> None:
    pid = st.session_state["participant_id"]
    db.insert_questionnaire(
        participant_id=pid,
        round_idx=ridx,
        ownership_json=json.dumps(
            {f"own{i}": answers[f"own{i}"] for i in range(1, _OWN_ITEMS + 1)}
        ),
        soa_json=json.dumps(
            {f"soa{i}": answers[f"soa{i}"] for i in range(1, _SOA_ITEMS + 1)}
        ),
        tlx_json=json.dumps(
            {f"tlx{i}": answers[f"tlx{i}"] for i in range(1, _TLX_ITEMS + 1)}
        ),
        intent_violation=answers["violation"],
        imagine_match=answers["imagine"],
        shot_annotations_json=json.dumps(shot_annotations, ensure_ascii=False),
    )
    if ridx == _ATTENTION_ROUND:
        db.update_participant(
            pid, attention_ok=int(answers.get("attention") == _ATTENTION_EXPECTED)
        )
    state.log_event("questionnaire_submit")
    state.advance_round()
