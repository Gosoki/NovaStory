from __future__ import annotations

import base64
import html
from pathlib import Path

import streamlit as st

from i18n import get_lang, t
from views import _storyboard

# "How it works" page, shown once after consent and before the background
# questionnaire. Standardizes onboarding: every participant reads the same short
# flow overview, sees that their idea can be any form/length (worked example as
# separate "answer field" mockups), and sees a sample of the storyboard the AI
# ultimately produces. Kept condition- and content-neutral: the example uses a
# NON-experimental topic (and says the real topic comes next round), so it
# teaches form/length, not content, and never adds E-style structured elicitation
# that would prime the shared intent step.

# A real gpt-4o-mini storyboard generated from the (rough) oversleeping example,
# embedded statically so the page needs no live API call. Rendered with the same
# 絵コンテ sheet participants actually get, so they see the true final output.
_SAMPLE = {
    "ja": (
        "1. 【秒数】5秒 【カメラ】クローズアップ 【画面】ベッドの中で寝ている主人公、目覚まし時計が止まっている 【セリフ・音】静かな寝室、アラームが鳴らない\n"
        "2. 【秒数】6秒 【カメラ】引き 【画面】主人公が慌てて起き上がり、時計を見て驚く 【セリフ・音】「えっ、もうこんな時間!?」と焦った声\n"
        "3. 【秒数】4秒 【カメラ】動き 【画面】外に飛び出して全速力で走る主人公 【セリフ・音】息を切らしながら「間に合わなきゃ！」"
    ),
    "zh": (
        "1. 【时长】5 秒 【拍法】特写 【画面描写】特写一只静止的闹钟,指针指向 9 点,屏幕没有亮 【台词/音效】安静的房间,随后响起“嘀嗒”的钟声\n"
        "2. 【时长】6 秒 【拍法】推镜头 【画面描写】镜头推进,一个人头发凌乱、慌忙起床抓起衣服,表情焦急 【台词/音效】自言自语:“怎么会睡过头了！”\n"
        "3. 【时长】4 秒 【拍法】动镜头 【画面描写】人物匆忙奔出房门,手里提着包,背景模糊成街道 【台词/音效】急促的脚步声与喘息声"
    ),
    "en": (
        "1. 【Duration】5 s 【Shot】close-up 【Visual】An alarm clock reading 9:15, its screen dark and silent 【Audio】A faint alarm tone, then silence\n"
        "2. 【Duration】6 s 【Shot】wide 【Visual】The hero jolts up, throws off the blankets and scrambles to get dressed 【Audio】Hurried footsteps, clothes rustling\n"
        "3. 【Duration】4 s 【Shot】tight 【Visual】Running down the street, adjusting a tie while glancing at their watch 【Audio】Breathless panting, distant city sounds"
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

_EX_CSS = """
<style>
.nsx-lead{margin:.2rem 0 .7rem;font-weight:700;font-size:1.02rem;
  display:inline-block;border-bottom:2px solid rgba(37,99,235,.65);padding-bottom:2px}
.nsx-wrap{margin:.3rem 0 .7rem}
.nsx-label{font-size:.8rem;opacity:.6;margin:0 0 4px 3px}
.nsx-field{border:1px solid rgba(128,128,128,.45);border-radius:8px;
  background:rgba(128,128,128,.12);padding:9px 12px;
  font-size:.9rem;line-height:1.55;white-space:pre-wrap}
</style>
"""


def _example(label: str, text: str) -> str:
    return (
        f'<div class="nsx-wrap"><div class="nsx-label">{html.escape(label)}</div>'
        f'<div class="nsx-field">{html.escape(text)}</div></div>'
    )


def render() -> None:
    st.header(t("intro.title"))
    with st.container(border=True):
        st.markdown(t("intro.flow"))
    st.info(t("intro.freedom"))

    boxes = "".join(
        _example(t(f"intro.ex{i}_label"), t(f"intro.ex{i}_text")) for i in (1, 2, 3, 4, 5)
    )
    st.markdown(
        f'{_EX_CSS}<div class="nsx-lead">{html.escape(t("intro.ex_lead"))}</div>{boxes}',
        unsafe_allow_html=True,
    )
    st.caption(t("intro.ex_note"))

    # Sample of the final output the AI produces from such an idea.
    st.markdown(t("intro.sample_lead"))
    _storyboard.render(_SAMPLE.get(get_lang(), _SAMPLE["ja"]), t("intro.sample_title"),
                       sketches=_SKETCH)

    st.caption(t("intro.reassure"))
    if st.button(t("intro.start"), type="primary", width="stretch"):
        st.session_state["stage"] = "screening"
        st.rerun()
