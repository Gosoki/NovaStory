from __future__ import annotations

import streamlit as st

from core import config, state
from i18n import get_lang, t
from views import _postgen, group_c, group_d, group_e, guidance, questionnaire


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
            _postgen.render(topic, cond)
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
    nums = "①②③④⑤"
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
    return 2 if not st.session_state["r_versions"] else 3


def _topic_card(topic: dict) -> None:
    lang = get_lang()
    st.subheader(t("round.topic_heading"))
    with st.container(border=True):
        st.markdown(f"**{state.topic_text(topic, 'title', lang)}**")
        st.write(state.topic_text(topic, "scenario", lang))
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
    )
    val = (st.session_state.get("_intent_input") or "").strip()
    if st.button(t("round.intent_submit"), type="primary", width="stretch"):
        if len(val) < config.MIN_INTENT_CHARS:
            st.error(t("errors.intent_too_short", n=config.MIN_INTENT_CHARS))
            return
        st.session_state["r_intent"] = val
        state.log_event("intent_submit", {"chars": len(val)})
        st.session_state["r_phase"] = "pipeline"
        st.rerun()
