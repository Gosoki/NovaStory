from __future__ import annotations

import streamlit as st

from core import db, shots, state

"""Researcher-only manual-testing helpers (rendered inside the sidebar's
researcher section). Works because the sidebar renders BEFORE the main area:
assigning to a main-area widget key here happens before that widget is
instantiated in the same run, which Streamlit allows."""

TEST_INTENTS = {
    "期末周渡劫": "主角发现卷子上的题目自己昨晚全都梦到过,但怎么也想不起答案",
    "错过的末班车": "主角错过末班车,索性跟着一支深夜跑团一路跑回了家",
    "五分钟的告白": "主角把要说的话写在毕业帽内侧,结果帽子被风吹走了",
}
_FALLBACK_INTENT = "主角在最普通的一天里发现了一件完全说不通的小事"
EDIT_SNIPPET = "\n(我的修改:结局反转——主角把这一切拍成视频发到了网上,火了)"


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
        st.session_state.get("lang", "zh"), demographics, screening, passed=True
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
        st.session_state["_intent_input"] = TEST_INTENTS.get(
            rd["topic"]["title"], _FALLBACK_INTENT
        )
    elif phase == "pipeline":
        if cond == "E" and st.session_state["r_dissent"] and not st.session_state["r_adjudication"]:
            st.session_state["_mm_choice"] = "transform"
            st.session_state["_mm_reason"] = "(测试)反转可以,但保留温情基调"
        elif cond in ("D", "E") and st.session_state["r_outline_ai"] and not st.session_state["r_final"]:
            base = (
                st.session_state.get("_outline_edit")
                or st.session_state["r_outline_user"]
                or st.session_state["r_outline_ai"]
            )
            if EDIT_SNIPPET.strip() not in base:
                st.session_state["_outline_edit"] = base + EDIT_SNIPPET
    elif phase == "questionnaire":
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
        parsed = shots.parse_shots(st.session_state["r_final"])
        if parsed:
            for s in parsed:
                st.session_state[f"_q_shot{s['idx']}_{ridx}"] = "mine"
        else:
            st.session_state[f"_q_whole_{ridx}"] = "mine"
