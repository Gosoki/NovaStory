from __future__ import annotations

import base64
import html
from pathlib import Path

import streamlit as st

from core import state
from i18n import get_lang, t
from views import _storyboard

# "How it works" page. Shown once, AFTER the background questionnaire and
# immediately before round 1 (2026-09-01 §4: the two pages were swapped so the
# briefing is the last thing a participant reads before creating, rather than
# being pushed out of memory by a 12-item form). Standardizes onboarding: every
# participant reads the same short flow overview, sees that their idea can be
# any form/length (worked example as separate "answer field" mockups), and sees
# a sample of the storyboard the AI ultimately produces. Kept condition- and
# content-neutral: the example uses a NON-experimental topic (and says the real
# topic comes next), so it teaches form/length, not content, and never adds
# E-style structured elicitation that would prime the shared intent step.

# A real gpt-4o-mini storyboard generated from the (rough) oversleeping example,
# embedded statically so the page needs no live API call. Rendered with the same
# 絵コンテ sheet participants actually get, so they see the true final output —
# and in the same two-line-per-shot layout the prompts now ask for (§9).
_SAMPLE = {
    "ja": (
        "1.\n"
        "【画面】ベッドで眠っている主人公。枕元の目覚まし時計は止まったまま\n"
        "【秒数】5秒 【カメラ】クローズアップ 【セリフ・音】静かな寝室。アラームは鳴らない\n"
        "2.\n"
        "【画面】主人公が慌てて起き上がり、時計を見て驚く\n"
        "【秒数】6秒 【カメラ】引き 【セリフ・音】「えっ、もうこんな時間!?」と焦った声\n"
        "3.\n"
        "【画面】外へ飛び出して全速力で走る主人公\n"
        "【秒数】4秒 【カメラ】動き 【セリフ・音】息を切らしながら「間に合わなきゃ！」"
    ),
    "zh": (
        "1.\n"
        "【画面描写】主角在床上睡着,床头的闹钟停着没响\n"
        "【时长】5 秒 【拍法】特写 【台词/音效】安静的房间,随后响起“嘀嗒”的钟声\n"
        "2.\n"
        "【画面描写】镜头推进,一个人头发凌乱、慌忙起床抓起衣服,表情焦急\n"
        "【时长】6 秒 【拍法】推镜头 【台词/音效】自言自语:“怎么会睡过头了！”\n"
        "3.\n"
        "【画面描写】人物匆忙奔出房门,手里提着包,背景模糊成街道\n"
        "【时长】4 秒 【拍法】动镜头 【台词/音效】急促的脚步声与喘息声"
    ),
    "en": (
        "1.\n"
        "【Visual】The hero asleep in bed; the alarm clock on the nightstand never went off\n"
        "【Duration】5 s 【Shot】close-up 【Audio】A faint alarm tone, then silence\n"
        "2.\n"
        "【Visual】The hero jolts up, throws off the blankets and scrambles to get dressed\n"
        "【Duration】6 s 【Shot】wide 【Audio】Hurried footsteps, clothes rustling\n"
        "3.\n"
        "【Visual】Running down the street, adjusting a tie while glancing at their watch\n"
        "【Duration】4 s 【Shot】tight 【Audio】Breathless panting, distant city sounds"
    ),
}

# Sample storyboard illustrations (gpt-image-1, oversleeping example), compressed
# and embedded as base64 data-URIs so the page stays self-contained (CSP-safe, no
# external requests). Files live in assets/intro_sample/. A missing file degrades
# to the empty "coming soon" frame.
_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "intro_sample"


def _img(path: Path) -> str:
    try:
        b64 = base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return ""
    return f'<img src="data:image/jpeg;base64,{b64}" alt=""/>'


_SKETCH = [_img(_ASSET_DIR / f"shot{i}.jpg") for i in (1, 2, 3)]

# `.nsx-lead` is the page's one emphasis style (accent colour + underline) and is
# shared by both lead-ins so they read as the same kind of sentence (§2, §5).
# The colours come from --ns-accent / --ns-dim (app.py), which are split by
# prefers-color-scheme: a single pair of values fails WCAG AA on one of the two
# themes, and the participant's OS picks the theme, not us.
# `.nsx-label` carries its dimming in the colour, not in `opacity` — opacity
# composites onto children, so the "例1" badge could not be brought back to full
# strength inside a faded parent.
_EX_CSS = """
<style>
.nsx-lead{margin:.9rem 0 .75rem;font-weight:700;font-size:1.02rem;color:var(--ns-accent,#2563eb);
  display:inline-block;border-bottom:2px solid currentColor;padding-bottom:2px}
.nsx-wrap{margin:0 0 1.05rem}
.nsx-label{font-size:var(--ns-fs-note,.82rem);color:var(--ns-dim,#6e6e6e);margin:0 0 5px 2px}
.nsx-no{font-weight:700;color:var(--ns-accent,#2563eb);margin-right:.5em}
.nsx-field{border:1px solid var(--ns-line,rgba(128,128,128,.45));
  border-radius:var(--ns-radius,8px);
  background:var(--ns-soft,rgba(128,128,128,.12));padding:9px 12px;
  font-size:.9rem;line-height:1.55;white-space:pre-wrap}
</style>
"""


def _example(n: int, label: str, text: str) -> str:
    return (
        f'<div class="nsx-wrap"><div class="nsx-label">'
        f'<span class="nsx-no">{html.escape(t("intro.ex_no", n=n))}</span>'
        f"{html.escape(label)}</div>"
        f'<div class="nsx-field">{html.escape(text)}</div></div>'
    )


def _lead(key: str) -> str:
    return f'<div class="nsx-lead">{html.escape(t(key))}</div>'


def render() -> None:
    # intro_shown → intro_continue is the dwell time on the standardized
    # onboarding; a subject who clicks through in 2s did not get the briefing.
    state.log_intake_event("intro_shown")
    st.header(t("intro.title"))
    with st.container(border=True):
        st.markdown(t("intro.flow"))
    st.info(t("intro.freedom"))

    boxes = "".join(
        _example(i, t(f"intro.ex{i}_label"), t(f"intro.ex{i}_text")) for i in (1, 2, 3, 4, 5)
    )
    st.markdown(f'{_EX_CSS}{_lead("intro.ex_lead")}{boxes}', unsafe_allow_html=True)
    st.caption(t("intro.ex_note"))

    # Sample of the final output the AI produces from such an idea — half size,
    # so the briefing page isn't dominated by a full-width storyboard table.
    st.markdown(_lead("intro.sample_lead"), unsafe_allow_html=True)
    _storyboard.render(_SAMPLE.get(get_lang(), _SAMPLE["ja"]), t("intro.sample_title"),
                       sketches=_SKETCH, mini=True)

    st.caption(t("intro.reassure"))
    if st.button(t("intro.start"), type="primary", width="stretch"):
        state.log_intake_event("intro_continue")
        # The round clock starts HERE, not at screening: start_rounds logs
        # round_start, and t_read_intent is measured from it. Parking the
        # participant on this page with the clock already running would fold the
        # whole briefing into round 1's reading time.
        state.start_rounds()
        st.rerun()
