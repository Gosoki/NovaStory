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
.sb-frame{position:relative;aspect-ratio:16/9;border:1.5px solid #3f3f3f;background:#fff;
  display:flex;align-items:center;justify-content:center;
  box-shadow:inset 0 0 0 3px #fff,inset 0 0 0 4px #ececec}
.sb-frame .lbl{color:#c3bfb4;font-style:italic;font-size:.82rem}
.sb-frame .secs{position:absolute;right:5px;bottom:4px;font-size:.72rem;color:#6a6a6a;
  background:rgba(255,255,255,.85);padding:0 3px;border-radius:2px}
.sb-frame img{width:100%;height:100%;object-fit:cover;display:block}
table.sb-tbl .cell{white-space:pre-wrap;word-break:break-word;line-height:1.42}
tr.sb-blank td{height:74px}
tr.sb-blank .sb-frame .lbl{display:none}
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
    Falls back to plain text (bordered box) when the script doesn't parse."""
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
    st.markdown(
        f'{_SB_CSS}<div class="sb-sheet">'
        f'<div class="sb-hd"><span class="sb-ttl">{html.escape(t("storyboard.title"))}</span>'
        f'<span class="sb-sub">{html.escape(subtitle)}</span></div>'
        f'<table class="sb-tbl"><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>'
        f"</div>",
        unsafe_allow_html=True,
    )
