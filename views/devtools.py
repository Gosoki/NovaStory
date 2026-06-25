from __future__ import annotations

import streamlit as st

from core import db, shots, state
from i18n import t

"""Researcher-only manual-testing helpers (rendered inside the sidebar's
researcher section). Works because the sidebar renders BEFORE the main area:
assigning to a main-area widget key here happens before that widget is
instantiated in the same run, which Streamlit allows."""

TEST_INTENTS = {  # keyed by zh title (researcher tests in zh)
    "下不去的满员电车": "主角终于挤到门边,门却在他面前关上了",
    "打工最后一天的那句话": "主角把想说的话写在了递出去的最后一杯咖啡的杯套上",
    "放榜的早晨": "主角不敢看公告栏,先盯着周围人的表情猜自己的结果",
}
_FALLBACK_INTENT = "主角在最普通的一天里发现了一件完全说不通的小事"
EDIT_SNIPPET = "\n(我的修改:结局反转——主角把这一切拍成视频发到了网上,火了)"
REVISION_SAMPLE = "(测试)整体更搞笑一点,最后一镜加个反转"


def render() -> None:
    st.subheader("🧪 测试工具")
    stage = st.session_state.get("stage")
    if stage in ("consent", "screening"):
        if st.button("跳过同意+筛查(注入测试被试)", width="stretch"):
            _skip_intake()
            st.rerun()
    elif stage == "rounds":
        rd = state.current_round()
        plan = " → ".join(
            f"R{i + 1}:{r['condition']}" for i, r in enumerate(st.session_state["round_plan"])
        )
        st.caption(f"序列 seq={st.session_state['seq']} | {plan}")
        st.caption(
            f"当前:第 {st.session_state['round_idx']} 轮 · 条件 **{rd['condition']}** · "
            f"阶段 {st.session_state['r_phase']}"
        )
        st.button("一键填充当前页输入", width="stretch", on_click=_fill_current)
        cols = st.columns(3)
        for col, cond in zip(cols, ("C", "D", "E")):
            col.button(
                f"切到 {cond}",
                width="stretch",
                disabled=cond == rd["condition"],
                on_click=_switch_condition,
                args=(cond,),
            )
        st.caption("切换会重置本轮已有输入(从写创意重新开始)。")
    st.caption("成套输入:samples/test_inputs.md;全自动回归:scripts/dev_smoke_e2e.py")


def _skip_intake() -> None:
    demographics = {"age": "18-24", "gender": "不愿透露", "ai_freq": "几乎每天", "dev": True}
    screening = {
        "published": "从未发布过", "background": "no", "written": "no",
        "self_rating": 1, "quiz_correct": 0, "is_novice": True, "dev": True,
    }
    pid, seq = db.insert_participant(
        st.session_state.get("lang", "ja"), demographics, screening, passed=True
    )
    state.begin_rounds(pid, seq)


def _switch_condition(cond: str) -> None:
    """Dev-only: swap the current round's condition and restart the round."""
    idx = st.session_state["round_idx"] - 1
    st.session_state["round_plan"][idx]["condition"] = cond
    state.reset_round_payload()
    state.log_event("dev_switch_condition", {"to": cond})


def _fill_current() -> None:
    phase = st.session_state["r_phase"]
    rd = state.current_round()
    cond = rd["condition"]
    if phase == "intent":
        # TEST_INTENTS is keyed by the zh title; topic titles are now {ja,zh} dicts.
        key = state.topic_text(rd["topic"], "title", "zh")
        st.session_state["_intent_input"] = TEST_INTENTS.get(key, _FALLBACK_INTENT)
    elif phase == "guidance":
        _fill_guidance()
    elif phase == "postgen":
        cur = st.session_state.get("_script_edit") or state.current_script()
        if EDIT_SNIPPET.strip() not in cur:
            st.session_state["_script_edit"] = cur + EDIT_SNIPPET
        if cond == "D":
            st.session_state["_revision_input"] = REVISION_SAMPLE
    elif phase == "questionnaire":
        _fill_questionnaire()


def _fill_guidance() -> None:
    """Answer every question (first option; one custom; one AI-decide) and jump
    to the last question so a single click on 完成作答 finishes the round."""
    qs = st.session_state["r_g_questions"]
    if not qs:
        return
    answers = {}
    for i, q in enumerate(qs):
        options = q.get("options") or []
        if i == 1:
            answers[i] = {"opt": None, "custom": "(测试)我自己写的方向"}
        elif i == 2 and options:
            answers[i] = {"opt": t("guidance.ai_decide"), "custom": ""}
        elif options:
            answers[i] = {"opt": options[0], "custom": ""}
        else:
            answers[i] = {"opt": None, "custom": "(测试)开放回答"}
    st.session_state["r_g_answers"] = answers
    st.session_state["r_g_idx"] = len(qs) - 1


def _fill_questionnaire() -> None:
    ridx = st.session_state["round_idx"]
    for i in range(1, 4):
        st.session_state[f"_q_own{i}_{ridx}"] = 5
    for i in range(1, 3):
        st.session_state[f"_q_soa{i}_{ridx}"] = 4
    st.session_state[f"_q_tlx1_{ridx}"] = 3
    st.session_state[f"_q_violation_{ridx}"] = 2
    st.session_state[f"_q_imagine_{ridx}"] = 6
    if ridx == 2:  # attention check round
        st.session_state[f"_q_attention_{ridx}"] = 2
    mine_label = t("q.tag_mine")  # shot widgets use localized labels as options
    parsed = shots.parse_shots(state.current_script())
    if parsed:
        for s in parsed:
            st.session_state[f"_q_shot{s['idx']}_{ridx}"] = mine_label
    else:
        st.session_state[f"_q_whole_{ridx}"] = mine_label
