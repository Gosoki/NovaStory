from __future__ import annotations

import json

import streamlit as st

from core import db, shots, state
from i18n import t

_SCALE = list(range(1, 8))
_SCALE_WIDTH = 600      # px; width of the 1-7 button row + aligned anchors (tunable)
_OWN_ITEMS = 3          # q.own1..own3 (trimmed for session length)
_SOA_ITEMS = 2          # q.soa1..soa2
_TLX_ITEMS = 1          # q.tlx1
_ATTENTION_ROUND = 2    # attention check embedded in round 2's questionnaire
_ATTENTION_EXPECTED = 2
_SHOT_TAGS = ("mine", "ai_ok", "ai_against")

# Endpoint (+ optional midpoint) labels shown under each 1-7 scale. Most items
# are agreement-type; violation/imagine are intensity scales with their own poles.
_ANCHOR_SETS = {
    "agree": ("anchor_disagree", "anchor_neutral", "anchor_agree"),
    "violation": ("anchor_viol_low", "anchor_neutral", "anchor_viol_high"),
    "imagine": ("anchor_imag_low", "anchor_neutral", "anchor_imag_high"),
}


def _anchor_html(text: str, align: str) -> str:
    return (
        f"<div style='text-align:{align};color:rgba(140,140,140,0.95);"
        f"font-size:0.78rem;line-height:1.15'>{text}</div>"
    )


def _scale_anchors(kind: str | None) -> None:
    """Anchor labels aligned under the scale: left / (center) / right, inside a
    container the same width as the button row so they line up with 1 / mid / 7."""
    if not kind:
        return
    left, mid, right = _ANCHOR_SETS[kind]
    with st.container(width=_SCALE_WIDTH):
        c1, c2, c3 = st.columns(3)
        c1.markdown(_anchor_html(t(f"q.{left}"), "left"), unsafe_allow_html=True)
        if mid:
            c2.markdown(_anchor_html(t(f"q.{mid}"), "center"), unsafe_allow_html=True)
        c3.markdown(_anchor_html(t(f"q.{right}"), "right"), unsafe_allow_html=True)


def render() -> None:
    ridx = st.session_state["round_idx"]
    # Show the finished script above the questionnaire so the participant can
    # refer to it while answering.
    with st.container(border=True):
        st.caption(t("q.script_review"))
        st.markdown(state.current_script())
    st.subheader(t("q.title"))
    st.caption(t("q.hint"))

    answers: dict[str, object] = {}
    missing = False

    def likert(key: str, label: str, anchors: str | None = "agree") -> None:
        nonlocal missing
        st.markdown(label)
        val = st.segmented_control(
            label, _SCALE, selection_mode="single", key=f"_q_{key}_{ridx}",
            width=_SCALE_WIDTH, label_visibility="collapsed",
        )
        _scale_anchors(anchors)
        if val is None:
            missing = True
        answers[key] = val
        st.divider()

    for i in range(1, _OWN_ITEMS + 1):
        likert(f"own{i}", t(f"q.own{i}"))
    for i in range(1, _SOA_ITEMS + 1):
        likert(f"soa{i}", t(f"q.soa{i}"))
    if ridx == _ATTENTION_ROUND:
        likert("attention", t("q.attention"), anchors=None)
    for i in range(1, _TLX_ITEMS + 1):
        likert(f"tlx{i}", t(f"q.tlx{i}"))
    likert("violation", t("q.violation"), anchors="violation")
    likert("imagine", t("q.imagine"), anchors="imagine")

    # ---- per-shot intent annotation ----
    # Options are the localized labels themselves (no format_func): a
    # segmented_control with format_func inside a loop is not reliably
    # AppTest-drivable across reruns. Selection is mapped back to the tag key.
    st.subheader(t("q.shots_title"))
    tag_labels = [t(f"q.tag_{c}") for c in _SHOT_TAGS]
    lbl2tag = dict(zip(tag_labels, _SHOT_TAGS))
    parsed = shots.parse_shots(state.current_script())
    shot_annotations: list[dict] = []
    if parsed:
        st.caption(t("q.shots_hint"))
        for s in parsed:
            with st.container(border=True):
                st.markdown(_shot_preview(s))
            sel = st.segmented_control(
                t("q.shot_tag_label"), tag_labels, selection_mode="single",
                key=f"_q_shot{s['idx']}_{ridx}",
            )
            if sel is None:
                missing = True
            shot_annotations.append({"shot": s["idx"], "tag": lbl2tag.get(sel)})
            st.divider()
    else:
        sel = st.segmented_control(
            t("q.whole_tag_label"), tag_labels, selection_mode="single",
            key=f"_q_whole_{ridx}",
        )
        if sel is None:
            missing = True
        shot_annotations.append({"shot": 0, "tag": lbl2tag.get(sel)})

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
