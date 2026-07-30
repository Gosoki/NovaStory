from __future__ import annotations

import html

import streamlit as st

from core import shots
from i18n import t

# Storyboard preview sheet, styled like a real 絵コンテ / 分镜纸: circled cut
# numbers, a 16:9 empty picture frame per shot (the 画面 column — a generated
# sketch there is future work, "coming soon"), then action / dialogue columns on
# a paper-like sheet. Shared by the questionnaire (participant's finished script)
# and the intro page (a static sample). Falls back to plain text when the script
# doesn't parse into shots.
_SB_CSS = """
<style>
.sb-sheet{background:#fbfaf3;border:2px solid #3f3f3f;border-radius:5px;
  padding:12px 14px 16px;margin:.2rem 0 .9rem;box-shadow:0 1px 7px rgba(0,0,0,.13);overflow-x:auto}
.sb-sheet .sb-hd{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;
  border-bottom:2px solid #3f3f3f;padding-bottom:7px;margin-bottom:11px}
.sb-sheet .sb-ttl{font-size:1.06rem;font-weight:700;letter-spacing:.18em;color:#2a2a2a}
.sb-sheet .sb-sub{color:#7a7a7a;font-size:.83rem}
table.sb-tbl{border-collapse:collapse;width:100%;table-layout:fixed;font-size:.86rem;color:#2a2a2a}
table.sb-tbl th,table.sb-tbl td{border:1px solid #9a958c;padding:6px 8px;vertical-align:top}
table.sb-tbl thead th{background:rgba(63,63,63,.08);text-align:center;font-weight:600;letter-spacing:.05em}
table.sb-tbl th:nth-child(1){width:7%}table.sb-tbl th:nth-child(2){width:14%}
table.sb-tbl th:nth-child(3){width:33%}table.sb-tbl th:nth-child(4){width:28%}
table.sb-tbl th:nth-child(5){width:18%}
td.sb-no{text-align:center;vertical-align:middle}
td.sb-no .cut{display:inline-flex;align-items:center;justify-content:center;width:27px;height:27px;
  border:1.5px solid #3f3f3f;border-radius:50%;font-weight:700;font-size:1rem;font-family:Georgia,serif}
td.sb-pic{vertical-align:middle;padding:7px}
.sb-frame{position:relative;width:100%;height:0;padding-bottom:56.25%;
  border:1.5px solid #3f3f3f;background:#fff;overflow:hidden;
  box-shadow:inset 0 0 0 3px #fff,inset 0 0 0 4px #ececec}
.sb-frame .lbl{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  color:#c3bfb4;font-style:italic;font-size:.82rem;white-space:pre-line;text-align:center;padding:0 6px}
.sb-frame .secs{position:absolute;right:5px;bottom:4px;font-size:.72rem;color:#6a6a6a;
  background:rgba(255,255,255,.85);padding:0 3px;border-radius:2px;z-index:1}
.sb-frame img{position:absolute;top:0;left:0;width:100%;height:100%;object-fit:contain;display:block}
table.sb-tbl .cell{white-space:pre-wrap;word-break:break-word;line-height:1.42}
tr.sb-blank td{height:74px}
tr.sb-blank .sb-frame .lbl{display:none}
/* Scroll-triggered corner mode (questionnaire page).
   `position:sticky` was tried first but Streamlit's ancestor containers have
   overflow constraints that neutralise it — sheet just scrolls out with the
   page. So the sheet is `position:fixed` always; a sibling `.sb-slot`
   placeholder stays in flow to reserve the natural layout height, and
   questionnaire.py's JS observer sets `transform: translate(x, y) scale(s)`
   on the sheet every scroll tick so it visually TRACKS the slot (fake-inflow).
   Once the slot's top scrolls above the viewport top, the transform target
   swaps to the corner (top-right, 32% scale) and `.is-animating` briefly
   enables a CSS transition so the change plays smoothly; the class is
   removed after the transition so in-flow-tracking updates on scroll do
   NOT interpolate (else the sheet would lag behind the scroll). */
.sb-slot{position:relative}
.sb-sheet{position:fixed;top:0;left:0;margin:0!important;z-index:100;
  transform-origin:top right;will-change:transform;
  opacity:0;pointer-events:none}
.sb-sheet.is-positioned{opacity:1;transition:opacity .2s ease-out}
/* Transition is active only during the corner-pin animation OR when the
   sheet is settled at corner (so CSS :hover scale-up animates smoothly).
   In pure flow-tracking state (no is-animating, no is-corner) transition is
   off so scroll updates don't rubber-band. */
.sb-sheet.is-animating,
.sb-sheet.is-corner{
  transition:transform .45s cubic-bezier(.22,.7,.2,1),opacity .2s}
.sb-sheet.is-corner{
  transform:translate(var(--sb-tx,0px),var(--sb-ty,64px)) scale(.32);
  cursor:zoom-in;filter:drop-shadow(0 8px 28px rgba(0,0,0,.35));box-shadow:none}
/* Rule: mouse can interact ONLY when the sheet is SETTLED at the corner
   (`.is-corner` present, `.is-animating` absent). Every other state
   (in-flow, mid-shrink, mid-expand back) is transparent to the mouse, so
   no stray mouseenter can hijack an in-progress transform animation. */
.sb-sheet.is-corner:not(.is-animating){pointer-events:auto}
.sb-sheet.is-corner:not(.is-animating):hover{
  transform:translate(var(--sb-tx,0px),var(--sb-ty,64px)) scale(.95);
  cursor:zoom-out}
@media (max-width:900px){.sb-slot{display:contents}
  .sb-sheet{position:static;transform:none!important;opacity:1;
    pointer-events:auto;filter:none;margin:.2rem 0 .9rem!important}}
</style>
"""


def _frame(label: str, secs: str, sketch: str = "") -> str:
    sec = f'<span class="secs">{html.escape(secs)}</span>' if secs else ""
    if sketch:  # trusted, author-supplied SVG (only the intro sample uses this)
        return f'<div class="sb-frame">{sketch}{sec}</div>'
    lbl = f'<span class="lbl">{html.escape(label)}</span>' if label else ""
    return f'<div class="sb-frame">{lbl}{sec}</div>'


def render(script: str, subtitle: str, sketches: list[str] | None = None) -> None:
    """Render a finished script as a 絵コンテ sheet with the given subtitle.
    `sketches` (optional) is a per-shot list of trusted SVG strings drawn into
    the picture frames; without it the frames show the "coming soon" placeholder.
    Falls back to plain text (bordered box) when the script doesn't parse.

    The sheet is emitted inside a `.sb-corner-wrap` with a hidden
    `.sb-placeholder` sibling. The questionnaire page's scroll observer (see
    _install_sb_scroll_observer in questionnaire.py) toggles `.is-corner` on
    the sheet once the user scrolls past it — pinning it to the top-right as
    a hover-to-expand thumbnail — and sizes the placeholder to keep the
    questionnaire body from jumping upward when the sheet leaves the flow."""
    parsed = shots.parse_shots(script)
    if not parsed:
        st.caption(t("q.script_review"))
        with st.container(border=True):
            st.markdown(script or "—")
        return

    todo = t("storyboard.frame_todo")
    body = ""
    for i, s in enumerate(parsed, 1):
        sketch = sketches[i - 1] if sketches and i - 1 < len(sketches) else ""
        body += (
            f'<tr><td class="sb-no"><span class="cut">{i}</span></td>'
            f'<td><div class="cell">{html.escape(s.get("shot_type") or "—")}</div></td>'
            f'<td class="sb-pic">{_frame(todo, (s.get("duration") or "").strip(), sketch)}</td>'
            f'<td><div class="cell">{html.escape(s.get("visual") or "")}</div></td>'
            f'<td><div class="cell">{html.escape(s.get("audio") or "")}</div></td></tr>'
        )
    # Blank row after the numbered shots (row 4): an empty picture cell.
    body += (
        f'<tr class="sb-blank"><td class="sb-no"></td><td></td>'
        f'<td class="sb-pic">{_frame("", "")}</td><td></td><td></td></tr>'
    )
    header = "".join(
        f"<th>{html.escape(h)}</th>"
        for h in (t("storyboard.col_no"), t("storyboard.col_shot"), t("storyboard.col_frame"),
                  t("storyboard.col_plot"), t("storyboard.col_line"))
    )
    sheet = (
        f'<div class="sb-slot">'
        f'<div class="sb-sheet">'
        f'<div class="sb-hd"><span class="sb-ttl">{html.escape(t("storyboard.title"))}</span>'
        f'<span class="sb-sub">{html.escape(subtitle)}</span></div>'
        f'<table class="sb-tbl"><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>'
        f"</div>"
        f"</div>"
    )
    st.markdown(f"{_SB_CSS}{sheet}", unsafe_allow_html=True)
