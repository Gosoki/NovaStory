from __future__ import annotations

import streamlit as st

from core import config, state
from i18n import t
from views import group_c, group_d, group_e, questionnaire


def render() -> None:
    rd = state.current_round()
    cond, topic = rd["condition"], rd["topic"]

    st.header(t("round.title", i=st.session_state["round_idx"], n=config.N_ROUNDS))
    _step_strip(cond)
    _topic_card(topic)

    phase = st.session_state["r_phase"]
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
    elif phase == "questionnaire":
        questionnaire.render()


def _step_strip(cond: str) -> None:
    """Visual stepper, symmetric across conditions (avoids differential demand:
    every condition previews its own steps the same way)."""
    steps = [t("round.step_intent")]
    if cond in ("D", "E"):
        steps.append(t("round.step_outline"))
    if cond == "E":
        steps.append(t("round.step_dissent"))
    steps.append(t("round.step_generate"))
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
    # pipeline
    if cond == "C":
        return 1
    if cond == "D":
        return 2 if st.session_state["r_final"] else 1
    # E
    if st.session_state["r_final"]:
        return 3
    if not st.session_state["r_dissent"]:
        return 1
    if not st.session_state["r_adjudication"]:
        return 2
    return 3


def _topic_card(topic: dict) -> None:
    with st.container(border=True):
        st.markdown(f"**{topic['title']}**")
        st.write(topic.get("scenario", ""))
        st.caption(
            t(
                "round.topic_spec",
                count=topic["shot_count"],
                total=topic["total_seconds"],
            )
        )


def _render_intent(cond: str, topic: dict) -> None:
    st.info(t(f"round.instr_{cond}", shot_count=topic["shot_count"]))
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
