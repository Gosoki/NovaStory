from __future__ import annotations

import html

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


def _render_anchors(kind: str | None) -> None:
    """Anchor labels under the scale: left / (center) / right, in ONE row whose
    width matches the button row so they line up with 1 / mid / 7.

    Deliberately a single flex row (styled by `.ns-anchor` in app.py), not
    `st.columns(3)`: Streamlit stacks columns vertically below ~640px, which on a
    phone turned every scale's three anchors into a diagonal staircase of three
    separate lines — and once 「非常にそう思う」 sits on its own line far under the
    buttons, the participant can no longer tell which END of the scale it labels.
    On a 7-point item whose meaning IS its anchors, that is the instrument losing
    its calibration, not a cosmetic issue. (Verified on a 390px viewport.)"""
    if not kind:
        return
    left, mid, right = _ANCHOR_SETS[kind]
    cells = [f'<span class="a-l">{html.escape(t(f"q.{left}"))}</span>']
    if mid:
        cells.append(f'<span class="a-m">{html.escape(t(f"q.{mid}"))}</span>')
    cells.append(f'<span class="a-r">{html.escape(t(f"q.{right}"))}</span>')
    st.markdown(
        f'<div class="ns-anchor" style="max-width:{WIDTH}px">{"".join(cells)}</div>',
        unsafe_allow_html=True,
    )


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
