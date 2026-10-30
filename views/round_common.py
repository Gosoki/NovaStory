from __future__ import annotations

import html
import re

import streamlit as st

from core import config, prompts, state
from i18n import get_lang, t
from views import _postgen, _scroll, _tour, group_c, group_d, group_e, group_f, guidance, interview, questionnaire


def render() -> None:
    rd = state.current_round()
    cond, topic = rd["condition"], rd["topic"]

    _round_header(cond)
    _topic_card(topic)

    phase = st.session_state["r_phase"]
    # The chat-style history sits at the very top of the loop phases so it stays
    # visible while answering guidance questions and while editing/revising.
    if phase == "guidance" or (phase == "postgen" and cond != "C"):
        _postgen.render_history()
    if phase == "intent":
        _render_intent(cond, topic)
    elif phase == "pipeline":
        st.info(t(f"round.instr_{cond}", shot_count=topic["shot_count"]))
        st.text_input(
            t("history.intent"),   # 已提交的创意回显:「长短形式随你」此时已无意义
            value=st.session_state["r_intent"],
            disabled=True,
        )
        if cond == "C":
            group_c.render_pipeline(topic)
        elif cond == "D":
            group_d.render_pipeline(topic)
        elif cond == "F":
            group_f.render_pipeline(topic)
            st.rerun()  # F: begin switched phase to "interview"
        else:
            group_e.render_pipeline(topic)
            st.rerun()  # E: begin_round switched phase to "guidance"
    elif phase == "guidance":
        guidance.render(topic)
    elif phase == "interview":
        interview.render(topic)
    elif phase == "postgen":
        if cond == "C":
            _postgen.render_readonly(topic)
        else:
            _postgen.render_editable(topic, cond)
    elif phase == "questionnaire":
        questionnaire.render()


_CHECK_SVG = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
              'stroke-linejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>')
_FILM_SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
             'stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/>'
             '<path d="M7.5 5v14M16.5 5v14M3 9.7h4.5M16.5 9.7H21M3 14.3h4.5M16.5 14.3H21"/></svg>')


def _round_header(cond: str) -> None:
    """轮次头:「第 i / n 轮」+ 每轮一格的进度 + 本轮的步骤条,一块。

    取代 v1 的 进度条 + 分割线 + h2 轮次标题 + markdown 步骤条(进度文字与 h2 写的是同一句;
    手机上这一摞占掉半屏)。每个条件照旧预告自己的步骤,文字都是原来的 i18n 键。
    手机上只显示当前一步的名字,其余是带编号的圆点(五个名字一行放不下,v1 是折成两行的箭头串)。"""
    i, n = st.session_state["round_idx"], state.rounds_total()
    steps = _steps(cond)
    cur = _current_step(cond, len(steps))

    pips = "".join(f'<i class="{"done" if k < i else "cur" if k == i else ""}"></i>' for k in range(1, n + 1))   # 装饰:轮次文字就在旁边
    items = []
    for k, label in enumerate(steps):
        cls, dot = ("done", _CHECK_SVG) if k < cur else ("cur", str(k + 1)) if k == cur else ("", str(k + 1))
        aria = ' aria-current="step"' if k == cur else ""
        items.append(f'<li class="{cls}"{aria}><span class="dot">{dot}</span>'
                     f'<span class="lbl">{html.escape(label)}</span></li>')
    title = html.escape(t("round.title", i=i, n=n))
    st.markdown(   # 轮次文字给读屏当二级标题(v1 是 st.header);视觉样式不变
        f'<nav class="ns-round" aria-label="{title}"><div class="ns-round-top">'
        f'<span class="ns-round-no" role="heading" aria-level="2">{title}</span>'
        f'<span class="ns-pips" aria-hidden="true">{pips}</span>'
        f'</div><ol class="ns-steps">{"".join(items)}</ol></nav>',
        unsafe_allow_html=True,
    )


def _steps(cond: str) -> list[str]:
    """本条件的步骤名(每个条件如实预告自己的流程)。"""
    steps = [t("round.step_intent")]
    if cond in ("E", "F"):
        steps.append(t("round.step_guidance"))
    steps.append(t("round.step_generate"))
    if cond in ("D", "E", "F"):
        steps.append(t("round.step_polish"))
    steps.append(t("round.step_questionnaire"))
    return steps


def round_fraction() -> float:
    """已做完的轮 + 本轮走到第几步,折成 0–1(app.py 页头那条全流程进度线用)。"""
    cond = state.current_round()["condition"]
    n_steps = len(_steps(cond))
    i, n = st.session_state["round_idx"], state.rounds_total()
    return ((i - 1) + _current_step(cond, n_steps) / n_steps) / n


def _current_step(cond: str, n_steps: int) -> int:
    phase = st.session_state["r_phase"]
    if phase == "intent":
        return 0
    if phase == "questionnaire":
        return n_steps - 1
    if cond == "C":
        return 1  # generate / view
    if cond == "D":
        return 1 if phase == "pipeline" else 2  # generate → polish
    # E / F(F 的采访只在生成之前,所以进得来就是第 1 步;问完之后那一屏就是在生成 → 第 2 步)
    if cond == "F" and phase == "interview" and st.session_state.get("r_iv_done"):
        return 2
    if phase in ("pipeline", "guidance", "interview"):
        # follow-up guidance rounds happen mid-polish
        return 1 if not st.session_state["r_versions"] else 3
    return 3   # postgen:进得来就一定已有版本(生成成功 / 取消追问都以此为前提)→ polish


# 句末标点 + 收尾破折号。choices 本身是完整的一句(「…还是开口问路。」/「…それとも——。」),
# 直接接上「……」会得到「问路。……或是」这种两种终止符叠在一起的写法 —— 三种语言里都是错的。
_TAIL_RE = re.compile(r"(?:[。．.?？!！\s]+|[—―─]+)+$")


def _topic_card(topic: dict) -> None:
    """题目卡:眉标(今回のお題)→ 题名 → 情境 → 选择示例(弱化)→ 规格。文字与 v1 一字不差,
    v1 的「今回のお題」小标题收进卡片当眉标(省一行 h3)。题库文本是纯文字(无 markdown),逐字转义。"""
    lang = get_lang()
    # 情境的两半必须同语言解析(prompts.situation_lang),否则半译的题库会让
    # 被试看到「英文设定 + 日文选择」,而模型拿到的也是同一份混语前提。
    # 题名一并用它 —— 否则会出现「日文题名 + 英文情境」的另一种混语。
    slang = prompts.situation_lang(topic, lang)
    parts = [
        f'<div class="ns-eyebrow">{html.escape(t("round.topic_heading"))}</div>',
        f'<div class="ns-topic-title" role="heading" aria-level="3">{html.escape(state.topic_text(topic, "title", slang))}</div>',
        f'<div class="ns-topic-scn">{html.escape(state.topic_text(topic, "scenario", slang))}</div>',
    ]
    choices = state.topic_text(topic, "choices", slang)
    if choices:
        # Bracketed and greyed out on purpose (§15). Set in the same weight as
        # the setup sentence, the "map / scent / ask someone" list reads as
        # the three allowed stories; it is meant as one suggestion among many,
        # and a participant who narrows their idea to fit it is a participant
        # whose intent we partly wrote for them — which is exactly what the
        # fidelity measure is supposed to be measuring.
        parts.append(
            '<div class="ns-topic-choices">'
            + html.escape(t("round.topic_choices", choices=_TAIL_RE.sub("", choices),
                            free=t("round.topic_free")))
            + "</div>"
        )
    parts.append(
        f'<div class="ns-topic-spec">{_FILM_SVG}'
        f'{html.escape(t("round.topic_spec", count=topic["shot_count"], total=topic["total_seconds"]))}</div>'
    )
    st.markdown(f'<section class="ns-topic">{"".join(parts)}</section>', unsafe_allow_html=True)


def _render_intent(cond: str, topic: dict) -> None:
    # 本轮怎么做(各条件的操纵说明,一字未动)。v1 框头还有一行「📍 今回の流れ：」—— 与步骤条重复,去掉;
    # 包进 ns_flow 是给新手引导点亮用的(题目 → 这个框 → 输入框)。
    with st.container(key="ns_flow"):
        st.info(t(f"round.instr_{cond}", shot_count=topic["shot_count"]))
    st.markdown(
        t("round.intent_scope", count=topic["shot_count"], total=topic["total_seconds"])
    )
    st.text_area(
        t("round.intent_label"),
        key="_intent_input",
        placeholder=t("round.intent_placeholder"),
        height=90,
        max_chars=1000,   # 说明页说「长短都行」—— 这是防整段粘贴长文撑爆上下文的硬上限,不是引导
    )
    val = (st.session_state.get("_intent_input") or "").strip()
    # 新手引导(第 1 轮聚光圈两步、之后每轮闪一下):先题目、再输入框。纯前端,不占版面、不记事件
    _tour.intent_tour()
    if st.button(t("round.intent_submit"), type="primary", width="stretch"):
        if len(val) < config.MIN_INTENT_CHARS:
            st.error(t("errors.intent_too_short", n=config.MIN_INTENT_CHARS))
            return
        st.session_state["r_intent"] = val
        state.log_event("intent_submit", {"chars": len(val)})
        st.session_state["r_phase"] = "pipeline"
        _scroll.request()   # 提交创意 → 开始生成:整页换内容
        st.rerun()
