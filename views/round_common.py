from __future__ import annotations

import html
import re

import streamlit as st

from core import config, prompts, state
from i18n import get_lang, t
from views import _postgen, _scroll, group_c, group_d, group_e, guidance, questionnaire


def render() -> None:
    rd = state.current_round()
    cond, topic = rd["condition"], rd["topic"]

    st.header(t("round.title", i=st.session_state["round_idx"], n=config.N_ROUNDS))
    _step_strip(cond)
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
            t("round.intent_label"),
            value=st.session_state["r_intent"],
            disabled=True,
        )
        if cond == "C":
            group_c.render_pipeline(topic)
        elif cond == "D":
            group_d.render_pipeline(topic)
        else:
            group_e.render_pipeline(topic)
            st.rerun()  # E: begin_round switched phase to "guidance"
    elif phase == "guidance":
        guidance.render(topic)
    elif phase == "postgen":
        if cond == "C":
            _postgen.render_readonly(topic)
        else:
            _postgen.render_editable(topic, cond)
    elif phase == "questionnaire":
        questionnaire.render()


def _step_strip(cond: str) -> None:
    """Visual stepper; every condition previews its own steps the same way."""
    steps = [t("round.step_intent")]
    if cond == "E":
        steps.append(t("round.step_guidance"))
    steps.append(t("round.step_generate"))
    if cond in ("D", "E"):
        steps.append(t("round.step_polish"))
    steps.append(t("round.step_questionnaire"))

    cur = _current_step(cond, len(steps))
    # E 条件 5 格(事前意图快照屏已于 2026-09-01 撤销);备到 ⑩ 以免以后再加一步越界
    nums = "①②③④⑤⑥⑦⑧⑨⑩"
    parts = []
    for i, s_ in enumerate(steps):
        if i < cur:
            parts.append(f":green[✓ {s_}]")
        elif i == cur:
            parts.append(f"**:blue[▶ {nums[i]} {s_}]**")
        else:
            parts.append(f":gray[{nums[i]} {s_}]")
    st.markdown(" → ".join(parts))


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
    # E
    if phase in ("pipeline", "guidance"):
        # follow-up guidance rounds happen mid-polish
        return 1 if not st.session_state["r_versions"] else 3
    return 3   # postgen:进得来就一定已有版本(生成成功 / 取消追问都以此为前提)→ polish


# 句末标点 + 收尾破折号。choices 本身是完整的一句(「…还是开口问路。」/「…それとも——。」),
# 直接接上「……」会得到「问路。……或是」这种两种终止符叠在一起的写法 —— 三种语言里都是错的。
_TAIL_RE = re.compile(r"(?:[。．.?？!！\s]+|[—―─]+)+$")


def _topic_card(topic: dict) -> None:
    lang = get_lang()
    st.subheader(t("round.topic_heading"))
    with st.container(border=True):
        # 情境的两半必须同语言解析(prompts.situation_lang),否则半译的题库会让
        # 被试看到「英文设定 + 日文选择」,而模型拿到的也是同一份混语前提。
        # 题名一并用它 —— 否则会出现「日文题名 + 英文情境」的另一种混语。
        slang = prompts.situation_lang(topic, lang)
        st.markdown(f"**{state.topic_text(topic, 'title', slang)}**")
        st.write(state.topic_text(topic, "scenario", slang))
        choices = state.topic_text(topic, "choices", slang)
        if choices:
            # Bracketed and greyed out on purpose (§15). Set in the same weight as
            # the setup sentence, the "map / scent / ask someone" list reads as
            # the three allowed stories; it is meant as one suggestion among many,
            # and a participant who narrows their idea to fit it is a participant
            # whose intent we partly wrote for them — which is exactly what the
            # fidelity measure is supposed to be measuring.
            st.markdown(
                "<div style='color:var(--ns-dim,#6e6e6e);"
                "font-size:var(--ns-fs-note,.82rem);"
                "line-height:1.5;margin:-.35rem 0 .35rem'>"
                + html.escape(t("round.topic_choices",
                                choices=_TAIL_RE.sub("", choices),
                                free=t("round.topic_free")))
                + "</div>",
                unsafe_allow_html=True,
            )
        st.caption(
            t(
                "round.topic_spec",
                count=topic["shot_count"],
                total=topic["total_seconds"],
            )
        )


def _render_intent(cond: str, topic: dict) -> None:
    # This round's flow label + the detailed how-to, in one box.
    st.info(
        f"**{t('round.flow_label')}**\n\n"
        + t(f"round.instr_{cond}", shot_count=topic["shot_count"])
    )
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
    if st.button(t("round.intent_submit"), type="primary", width="stretch"):
        if len(val) < config.MIN_INTENT_CHARS:
            st.error(t("errors.intent_too_short", n=config.MIN_INTENT_CHARS))
            return
        st.session_state["r_intent"] = val
        state.log_event("intent_submit", {"chars": len(val)})
        st.session_state["r_phase"] = "pipeline"
        _scroll.request()   # 提交创意 → 开始生成:整页换内容
        st.rerun()
