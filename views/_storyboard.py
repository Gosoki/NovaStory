from __future__ import annotations

import html

import streamlit as st

from core import shots
from i18n import t

# ⚠️ 这张纸有**自己的一套配色**(奶白纸 #fbfaf3 / 墨线 #3f3f3f / 格线 #9a958c),
# 刻意不走 app.py 的 --ns-* 令牌:它模拟的是一张真实的絵コンテ用纸,底色固定、不随
# 明暗主题翻转。所以这里出现的颜色字面量是**子主题**,不是漏网的散落值 —— 改它之前
# 先确认你要改的是「纸」而不是「界面」。
#
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
.sb-sheet .sb-sub{color:#6a6a6a;font-size:.83rem}   /* 5.3:1 on #fbfaf3,过 AA */
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
  color:#6a6a6a;font-size:.82rem;white-space:pre-line;text-align:center;padding:0 6px}  /* 原 #c3bfb4 斜体只有 1.84:1 */
.sb-frame .secs{position:absolute;right:5px;bottom:4px;font-size:.72rem;color:#6a6a6a;
  background:rgba(255,255,255,.85);padding:0 3px;border-radius:2px;z-index:1}
.sb-frame img{position:absolute;top:0;left:0;width:100%;height:100%;object-fit:contain;display:block}
table.sb-tbl .cell{white-space:pre-wrap;word-break:break-word;line-height:1.42}
tr.sb-blank td{height:74px}
tr.sb-blank .sb-frame .lbl{display:none}
/* Scroll-triggered corner mode — ONLY for sheets marked `.sb-pin` (the
   questionnaire page, which injects the scroll observer) AND only once the
   observer has proved it works by putting `.sb-js` on <html>. Both gates matter:

   · `.sb-pin` keeps the intro page's static sample out of this entirely.
   · `:root.sb-js` makes the whole thing FAIL SAFE. Everything here hinges on JS
     positioning the sheet; the un-JS'd state must therefore be a plain, visible,
     in-flow table, never `position:fixed;opacity:0`. Otherwise any reason the
     script doesn't run — a CSP, a Streamlit change to st.html, an old version
     where `unsafe_allow_javascript` doesn't exist — silently leaves the
     participant with NO storyboard to answer the questionnaire against.

   `position:sticky` was tried first but Streamlit's ancestor containers have
   overflow constraints that neutralise it — sheet just scrolls out with the
   page. So the pinned sheet is `position:fixed` always; its `.sb-slot` PARENT
   stays in flow and is sized by the observer to reserve the sheet's height, and
   questionnaire.py's JS observer sets `transform: translate(x, y) scale(s)`
   on the sheet every scroll tick so it visually TRACKS the slot (fake-inflow).
   Once the slot's top scrolls above the viewport top, the transform target
   swaps to the corner (top-right, 32% scale) and `.is-animating` briefly
   enables a CSS transition so the change plays smoothly; the class is
   removed after the transition so in-flow-tracking updates on scroll do
   NOT interpolate (else the sheet would lag behind the scroll). */
:root.sb-js .sb-slot.sb-pin{position:relative}
:root.sb-js .sb-sheet.sb-pin{position:fixed;top:0;left:0;margin:0!important;z-index:100;
  transform-origin:top right;will-change:transform;
  opacity:0;pointer-events:none}
:root.sb-js .sb-sheet.sb-pin.is-positioned{opacity:1;transition:opacity .2s ease-out}
/* Kills every transition for one tick while the observer places a freshly
   rendered node (see `fresh` in questionnaire.py). A new node starts at
   transform:none = the viewport's top-left corner, and letting the corner
   transition run from there sweeps the full-size sheet over the questions. */
:root.sb-js .sb-sheet.sb-pin.sb-noanim{transition:none!important}
/* Transition is active only during the corner-pin animation OR when the
   sheet is settled at corner (so CSS :hover scale-up animates smoothly).
   In pure flow-tracking state (no is-animating, no is-corner) transition is
   off so scroll updates don't rubber-band. */
:root.sb-js .sb-sheet.sb-pin.is-animating,
:root.sb-js .sb-sheet.sb-pin.is-corner{
  transition:transform .32s cubic-bezier(.22,.7,.2,1),opacity .2s}
/* Ghost the sheet while it flies between flow and corner. By the time it pins,
   the sheet has usually scrolled well above the viewport, so the trip to the
   corner (y=64) drags the full-size sheet back DOWN across the questions. It
   only lasts one transition and never takes clicks (pointer-events:none), but
   it reads as "the storyboard covered my question", so make it see-through
   while it travels. Declared after .is-positioned so it wins at equal
   specificity. ANIM_MS in questionnaire.py must match the transform duration.
   `opacity 0s` on the way IN so the sheet is already faded on the very first
   frame of the flight (a fade-in would still be near-opaque while it crosses
   the top of the page); fading back to 1 is handled by .is-positioned's own
   .2s transition once this class comes off. */
:root.sb-js .sb-sheet.sb-pin.is-animating{opacity:.28;
  transition:transform .32s cubic-bezier(.22,.7,.2,1),opacity 0s}
:root.sb-js .sb-sheet.sb-pin.is-corner{
  transform:translate(var(--sb-tx,0px),var(--sb-ty,64px)) scale(.32);
  cursor:zoom-in;filter:drop-shadow(0 8px 28px rgba(0,0,0,.35));box-shadow:none}
/* Rule: mouse can interact ONLY when the sheet is SETTLED at the corner
   (`.is-corner` present, `.is-animating` absent). Every other state
   (in-flow, mid-shrink, mid-expand back) is transparent to the mouse, so
   no stray mouseenter can hijack an in-progress transform animation. */
:root.sb-js .sb-sheet.sb-pin.is-corner:not(.is-animating){pointer-events:auto}
/* Hold-to-zoom, not brush-to-zoom: the expanded sheet is nearly full size and
   DOES take clicks, so a pointer merely crossing the top-right corner on its
   way somewhere else must not blow it open over the questions. The delay
   applies on the way in only — releasing falls back to the rule above, which
   carries no delay, so it snaps shut the moment the pointer leaves. */
:root.sb-js .sb-sheet.sb-pin.is-corner:not(.is-animating):hover{
  transform:translate(var(--sb-tx,0px),var(--sb-ty,64px)) scale(.95);
  transition-delay:.35s;cursor:zoom-out}
/* Narrow screens opt out of pinning entirely: the slot becomes display:contents
   (no box → clientWidth 0 → measure() bails, so the observer stops touching the
   sheet) and the sheet is a plain in-flow element again. Everything the observer
   may have written while the window was still wide — inline width, a leftover
   .is-animating opacity, a transform — has to be forced back here, otherwise
   resizing across 900px leaves the sheet stuck at the old width or half faded. */
/* Half-size sheet — the intro page's static sample only.
   Implemented as a WIDTH CAP, not `zoom`. `zoom` scales lengths but not an
   auto-width block's share of its parent: the sheet would have stayed full
   width while its 0.86rem type shrank to ~6.9px — unreadable CJK, on the one
   page whose whole job is to show what the AI produces. A max-width halves the
   footprint (which is what "缩小50%" is asking for) and leaves the type alone,
   and it degrades correctly on a phone instead of needing its own opt-out. */
.sb-mini .sb-sheet{max-width:50%}
@media (max-width:900px){.sb-mini .sb-sheet{max-width:100%}}
@media (max-width:900px){:root.sb-js .sb-slot.sb-pin{display:contents}
  :root.sb-js .sb-sheet.sb-pin{position:static;transform:none!important;opacity:1!important;
    width:auto!important;filter:none!important;cursor:auto!important;
    box-shadow:0 1px 7px rgba(0,0,0,.13)!important;
    pointer-events:auto;margin:.2rem 0 .9rem!important}}
/* ── 窄屏:表格 → 每镜一张卡 ────────────────────────────────────────────
   5 列的固定百分比(7/14/33/28/18%)是给 ~900px 宽的桌面表调的。放到 390px 的手机上
   就成了 27/55/129/109/70 px —— 实测截图里「No.」的表头竖排成 N / o / .,
   「クローズアップ」在 55px 里折成四行并压住镜号圆圈,「セリフ・音」整列变成一列
   单字带子(静 / か / な / 寝 / 室)。那是完全读不了的,而问卷页正要求被试**对照着
   这张表**回答所有权与保真 —— 读不了表,答的就不是同一道题。
   所以窄屏不再是表:每一镜变成一张卡,画框整宽,文字列带回自己的列名。 */
@media (max-width:700px){
  table.sb-tbl{display:block;table-layout:auto;font-size:.9rem}
  table.sb-tbl thead{display:none}
  table.sb-tbl tbody{display:block}
  table.sb-tbl tr{display:grid;grid-template-columns:auto 1fr;gap:0 10px;
    border:1px solid #9a958c;border-radius:4px;margin:0 0 10px;padding:8px 10px;
    background:rgba(255,255,255,.5)}
  table.sb-tbl td{display:block;border:0!important;padding:3px 0!important;width:auto!important}
  table.sb-tbl td.sb-no{grid-column:1;grid-row:1;align-self:center}
  table.sb-tbl td:nth-child(2){grid-column:2;grid-row:1;align-self:center;font-weight:600}
  table.sb-tbl td.sb-pic{grid-column:1/-1;padding:6px 0 2px!important}
  table.sb-tbl td:nth-child(4),table.sb-tbl td:nth-child(5){grid-column:1/-1}
  table.sb-tbl td:nth-child(4)::before,table.sb-tbl td:nth-child(5)::before{
    content:attr(data-col);display:block;font-size:.72rem;font-weight:700;
    /* #6a6a6a 在这张固定奶白纸(#fbfaf3)上是 5.3:1 过 AA;这里**不能**用 --ns-dim ——
       深色主题下它会解析成 #a3a3a3,而纸色是固定的,对比度掉到 ~2.2:1。 */
    letter-spacing:.04em;color:#6a6a6a;margin-top:4px}
  /* 画框在卡片模式下会独占整宽:3 镜里通常两三格是空占位(未开 OpenAI 时全是空),
     16:9 撑开就是三倍的纸高,被试要多滚两屏才看得完自己那 3 镜。限个宽。 */
  table.sb-tbl td.sb-pic .sb-frame{max-width:320px}
  /* 装饰用的空白第 4 行在手机上只是一张空卡,收起来 */
  table.sb-tbl tr.sb-blank{display:none}
  .sb-sheet{padding:10px 10px 12px}
  .sb-sheet .sb-ttl{font-size:.98rem;letter-spacing:.12em}
}
</style>
"""


def _frame(label: str, secs: str, sketch: str = "") -> str:
    sec = f'<span class="secs">{html.escape(secs)}</span>' if secs else ""
    if sketch:  # trusted, self-produced <img data:…> tag (intro sample / imagegen) — inserted unescaped
        return f'<div class="sb-frame">{sketch}{sec}</div>'
    lbl = f'<span class="lbl">{html.escape(label)}</span>' if label else ""
    return f'<div class="sb-frame">{lbl}{sec}</div>'


def render(script: str, subtitle: str, sketches: list[str] | None = None,
           pin: bool = False, mini: bool = False) -> None:
    """Render a finished script as a 絵コンテ sheet with the given subtitle.
    `sketches` (optional) is a per-shot list of trusted <img data:…> tags drawn into
    the picture frames; without it the frames show the "coming soon" placeholder.
    Falls back to plain text (bordered box) when the script doesn't parse.

    `pin=True` opts the sheet into scroll-pinning and REQUIRES the caller to also
    inject the scroll observer (questionnaire.py's _install_sb_scroll_observer):
    the sheet becomes `position:fixed` inside a `.sb-slot` placeholder, the
    observer tracks the slot every scroll tick and toggles `.is-corner` once the
    user scrolls past it — pinning it to the top-right as a hover-to-expand
    thumbnail — while keeping the slot exactly as tall as the sheet so the
    questionnaire body never slides up underneath it. Without `pin` the sheet is
    an ordinary in-flow element (the intro page's static sample).

    `mini=True` renders the sheet at half size (the intro sample; the real sheet
    is a wide table that dominated the briefing page)."""
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
        # data-col:窄屏堆叠成卡片后,用 ::before 把列名显回来(见 _SB_CSS 的 700px 段)
        body += (
            f'<tr><td class="sb-no"><span class="cut">{i}</span></td>'
            f'<td><div class="cell">{html.escape(s.get("shot_type") or "—")}</div></td>'
            f'<td class="sb-pic">'
            f'{_frame(todo, (s.get("duration") or "").strip(), sketch)}</td>'
            f'<td data-col="{html.escape(t("storyboard.col_plot"))}">'
            f'<div class="cell">{html.escape(s.get("visual") or "")}</div></td>'
            f'<td data-col="{html.escape(t("storyboard.col_line"))}">'
            f'<div class="cell">{html.escape(s.get("audio") or "")}</div></td></tr>'
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
    p = " sb-pin" if pin else ""
    sheet = (
        f'<div class="sb-slot{p}">'
        f'<div class="sb-sheet{p}">'
        f'<div class="sb-hd"><span class="sb-ttl">{html.escape(t("storyboard.title"))}</span>'
        f'<span class="sb-sub">{html.escape(subtitle)}</span></div>'
        f'<table class="sb-tbl"><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>'
        f"</div>"
        f"</div>"
    )
    if mini:
        sheet = f'<div class="sb-mini">{sheet}</div>'
    st.markdown(f"{_SB_CSS}{sheet}", unsafe_allow_html=True)
