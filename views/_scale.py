from __future__ import annotations

import streamlit as st

from i18n import t

# The study's single 7-point Likert widget. Every scale a subject meets —
# screening traits, the per-round questionnaire, the final survey — renders
# through here, so the visual grammar is identical throughout: same button-row
# width, same anchor line under 1 / mid / 7. Before this, screening and the
# final survey carried their anchors as "(1=…, 7=…)" inside the question text
# at a different width, so one response format looked like three different
# instruments across a single session.

SCALE = list(range(1, 8))
WIDTH = 600      # px; the button row and the anchor line under it share this width

# Endpoint (+ midpoint) label keys per scale type. Most items are
# agreement-type; the rest are intensity scales with their own poles.
_ANCHOR_SETS = {
    "agree": ("anchor_disagree", "anchor_neutral", "anchor_agree"),
    "violation": ("anchor_viol_low", "anchor_neutral", "anchor_viol_high"),
    "imagine": ("anchor_imag_low", "anchor_neutral", "anchor_imag_high"),
    "amount": ("anchor_amt_low", "anchor_amt_mid", "anchor_amt_high"),
    "skill": ("anchor_skill_low", "anchor_neutral", "anchor_skill_high"),
    "satisfied": ("anchor_sat_low", "anchor_neutral", "anchor_sat_high"),
}


def _anchor_html(text: str, align: str) -> str:
    return (
        f"<div style='text-align:{align};color:rgba(140,140,140,0.95);"
        f"font-size:0.78rem;line-height:1.15'>{text}</div>"
    )


def _render_anchors(kind: str | None) -> None:
    """Anchor labels aligned under the scale: left / (center) / right, inside a
    container the same width as the button row so they line up with 1 / mid / 7."""
    if not kind:
        return
    left, mid, right = _ANCHOR_SETS[kind]
    with st.container(width=WIDTH):
        c1, c2, c3 = st.columns(3)
        c1.markdown(_anchor_html(t(f"q.{left}"), "left"), unsafe_allow_html=True)
        if mid:
            c2.markdown(_anchor_html(t(f"q.{mid}"), "center"), unsafe_allow_html=True)
        c3.markdown(_anchor_html(t(f"q.{right}"), "right"), unsafe_allow_html=True)


def likert(label: str, key: str, *, anchors: str | None = "agree"):
    """One labelled 7-point item; returns the selection, None if unanswered.

    `anchors` names an _ANCHOR_SETS entry, or None for no anchor line (the
    attention check, which is an instruction rather than a scale)."""
    st.markdown(label)
    val = st.segmented_control(
        label, SCALE, selection_mode="single", key=key,
        width=WIDTH, label_visibility="collapsed",
    )
    _render_anchors(anchors)
    return val


def short(label: str, n: int = 18) -> str:
    """A compact, recognizable form of a long item label for the unanswered list."""
    label = label.strip()
    return label if len(label) <= n else label[:n] + "…"
