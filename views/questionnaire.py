from __future__ import annotations

import json

import streamlit as st

from core import db, imagegen, shots, state
from i18n import get_lang, t
from views import _storyboard

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
    "amount": ("anchor_amt_low", "anchor_amt_mid", "anchor_amt_high"),
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


def _short(label: str, n: int = 18) -> str:
    """A compact, recognizable form of a long item label for the unanswered list."""
    label = label.strip()
    return label if len(label) <= n else label[:n] + "…"




def _ai_q_best_pick(ridx: int):
    """E-only, optional: let the subject flag the guiding questions that helped —
    multi-select over every question asked this trial, each shown as
    'question → the answer you gave' so it is recognizable. Returns a list of
    {idx, dimension, question, chosen} (empty if none flagged), or None when <2
    were asked. Not added to `missing` — it is optional."""
    asked = [
        it for r in st.session_state.get("r_guidance_rounds", [])
        for it in r.get("items", [])
        if it.get("question")
    ]
    if len(asked) < 2:
        return None

    def _answer(it: dict) -> str:
        if it.get("ai_decided"):
            return t("guidance.ai_decide")
        return (it.get("chosen") or "").strip()

    st.markdown(t("q.ai_q_best"))
    st.caption(t("q.ai_q_best_ph"))
    chosen = []
    for i, it in enumerate(asked):
        ans = _answer(it)
        label = f"{it['question']} → {ans}" if ans else it["question"]
        if st.checkbox(label, key=f"_q_aqbest_{ridx}_{i}"):
            chosen.append({"idx": i, "dimension": it.get("dimension", "other"),
                           "question": it.get("question", ""), "chosen": ans})
    st.divider()
    return chosen


@st.fragment(run_every=2)
def _live_storyboard(script: str, subtitle: str, pid: int, ridx: int, n: int) -> None:
    """Poll the archive folder every 2s and show images as they finish; one full
    rerun once all are done so the outer render goes static and polling stops."""
    _storyboard.render(script, subtitle,
                       sketches=imagegen.frame_htmls(pid, ridx, n, t("storyboard.generating")))
    if imagegen.all_done(pid, ridx, n):
        st.rerun()  # full rerun → outer renders static, polling stops


def _render_storyboard_area(ridx: int) -> None:
    """Storyboard preview above the questionnaire. With OpenAI, the 画面 column
    fills with gpt-image-1 illustrations generated in the background AFTER submit
    (not during the creative task, so it doesn't touch t_pregen/t_postgen)."""
    script = state.current_script()
    subtitle = state.topic_text(state.current_round()["topic"], "title", get_lang())
    if imagegen.enabled():
        parsed = shots.parse_shots(script)
        if parsed:
            pid, n = st.session_state["participant_id"], len(parsed)
            if imagegen.all_done(pid, ridx, n):
                _storyboard.render(script, subtitle,
                                   sketches=imagegen.frame_htmls(pid, ridx, n, t("storyboard.generating")))
            else:
                imagegen.ensure_started(pid, ridx, parsed,
                                        st.session_state.get("api_key", ""),
                                        st.session_state.get("base_url", ""))
                _live_storyboard(script, subtitle, pid, ridx, n)
            return
    _storyboard.render(script, subtitle)


def render() -> None:
    ridx = st.session_state["round_idx"]
    # Show the finished script as a storyboard table above the questionnaire so
    # the participant can refer to it while answering.
    _render_storyboard_area(ridx)
    st.subheader(t("q.title"))
    st.caption(t("q.hint"))

    answers: dict[str, object] = {}
    missing: list[str] = []  # labels of unanswered items, named back to the user

    def likert(key: str, label: str, anchors: str | None = "agree") -> None:
        st.markdown(label)
        val = st.segmented_control(
            label, _SCALE, selection_mode="single", key=f"_q_{key}_{ridx}",
            width=_SCALE_WIDTH, label_visibility="collapsed",
        )
        _scale_anchors(anchors)
        if val is None:
            missing.append(_short(label))
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
    likert("sat", t("q.satisfaction"))
    # E-only: rate the quality of the AI's guiding questions (E is the only
    # condition where the AI asks structured questions). ai_q_amount = did it ask
    # too few / just right / too many; ai_q_best = which single question landed
    # (optional). Both exploratory, stored E-only (NULL for C/D).
    if state.current_round()["condition"] == "E":
        likert("ai_q_quality", t("q.ai_q_quality"))
        likert("ai_q_amount", t("q.ai_q_amount"), anchors="amount")
        answers["ai_q_best"] = _ai_q_best_pick(ridx)

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
                missing.append(t("q.shot_label", i=s["idx"]))
            shot_annotations.append({"shot": s["idx"], "tag": lbl2tag.get(sel)})
            st.divider()
    else:
        sel = st.segmented_control(
            t("q.whole_tag_label"), tag_labels, selection_mode="single",
            key=f"_q_whole_{ridx}",
        )
        if sel is None:
            missing.append(_short(t("q.whole_tag_label")))
        shot_annotations.append({"shot": 0, "tag": lbl2tag.get(sel)})

    if st.button(t("q.submit"), type="primary", width="stretch"):
        if missing:
            st.error(t("errors.unanswered", items=" / ".join(missing)))
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
        satisfaction=answers["sat"],
        ai_q_quality=answers.get("ai_q_quality"),  # E only; NULL for C/D
        ai_q_amount=answers.get("ai_q_amount"),    # E only; NULL for C/D
        ai_q_best_json=(json.dumps(answers["ai_q_best"], ensure_ascii=False)
                        if answers.get("ai_q_best") else None),
        shot_annotations_json=json.dumps(shot_annotations, ensure_ascii=False),
    )
    if ridx == _ATTENTION_ROUND:
        db.update_participant(
            pid,
            attention_ok=int(answers.get("attention") == _ATTENTION_EXPECTED),
            attention_raw=answers.get("attention"),  # keep the raw value (#31)
        )
    state.log_event("questionnaire_submit")
    state.advance_round()
