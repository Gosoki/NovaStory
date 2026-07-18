from __future__ import annotations

import streamlit as st

from core import db, llm, shots, state
from i18n import t

"""Researcher-only manual-testing helpers (rendered inside the sidebar's
researcher section). Works because the sidebar renders BEFORE the main area:
assigning to a main-area widget key here happens before that widget is
instantiated in the same run, which Streamlit allows."""

TEST_INTENTS = {  # keyed by zh title (researcher tests in zh)
    "捡到的失物（别人掉的东西）": "主角捡到一个钱包,犹豫再三,最后追上前面的人还了回去",
    "最后一口（分享还是独享）": "两个人都盯着最后一块蛋糕,主角假装大方让出去,对方却真的一口吃了",
    "陌生街道的第一步": "主角刚下车,深吸一口气,朝着完全陌生的方向迈出了第一步",
}
_FALLBACK_INTENT = "主角在最普通的一天里发现了一件完全说不通的小事"
EDIT_SNIPPET = "\n(我的修改:结局反转——主角把这一切拍成视频发到了网上,火了)"
REVISION_SAMPLE = "(测试)整体更搞笑一点,最后一镜加个反转"


def render() -> None:
    st.subheader(t("admin.tools_title"))
    _model_check()
    stage = st.session_state.get("stage")
    if stage in ("consent", "screening"):
        if st.button(t("admin.skip_intake"), width="stretch"):
            _skip_intake()
            st.rerun()
    elif stage == "rounds":
        rd = state.current_round()
        plan = " → ".join(
            f"R{i + 1}:{r['condition']}" for i, r in enumerate(st.session_state["round_plan"])
        )
        st.caption(t("admin.seq_line", seq=st.session_state["seq"], plan=plan))
        st.caption(
            t("admin.cur_line", i=st.session_state["round_idx"],
              cond=rd["condition"], phase=st.session_state["r_phase"])
        )
        st.button(t("admin.fill_current"), width="stretch", on_click=_fill_current)
        cols = st.columns(3)
        for col, cond in zip(cols, ("C", "D", "E")):
            col.button(
                t("admin.switch_to", cond=cond),
                width="stretch",
                disabled=cond == rd["condition"],
                on_click=_switch_condition,
                args=(cond,),
            )
        st.caption(t("admin.switch_hint"))
    st.caption(t("admin.test_refs"))


def _model_check() -> None:
    """Researcher connectivity probe — ping the configured model and report
    通/不通 + latency, so 接口繁忙/慢/挂 can be caught before a participant starts."""
    meta = llm.current_meta()
    st.caption(t("admin.model_line", model=meta["model"],
                 base=meta["base_url"] or t("admin.model_default")))
    if st.button(t("admin.model_ping"), width="stretch", key="btn_model_ping"):
        with st.spinner(t("admin.model_pinging")):
            ok, elapsed, detail = llm.ping()
        if ok:
            st.success(t("admin.ping_ok", s=f"{elapsed:.1f}", detail=detail[:40]))
            if elapsed > 30:
                st.warning(t("admin.ping_slow"))
        else:
            st.error(t("admin.ping_fail", s=f"{elapsed:.1f}", detail=detail[:200]))
    st.divider()


def _skip_intake() -> None:
    demographics = {"age_idx": 0, "gender_idx": 3, "ai_freq_idx": 3, "dev": True}
    screening = {
        "published_idx": 0, "background": "no", "written": "no",
        "self_rating": 1, "aiexp_idx": 0, "trust": 4, "own_trait": 4,
        "quiz_correct": 0, "is_novice": True, "dev": True,
    }
    pid, seq, token = db.insert_participant(
        st.session_state.get("lang", "ja"), demographics, screening, passed=True
    )
    state.begin_rounds(pid, seq, token)


def _switch_condition(cond: str) -> None:
    """Dev-only: swap the current round's condition and restart the round."""
    idx = st.session_state["round_idx"] - 1
    st.session_state["round_plan"][idx]["condition"] = cond
    state.reset_round_payload()
    state.log_event("dev_switch_condition", {"to": cond})
    # Fresh timing origin — r_events was wiped, so without a new round_start the
    # restarted round's t_read_intent / t_total would land NULL.
    state.log_event("round_start")


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
            answers[i] = {"opt": None, "custom": "(测试)我自己写的方向", "ai_decided": False}
        elif i == 2 and options:
            answers[i] = {"opt": t("guidance.ai_decide"), "custom": "", "ai_decided": True}
        elif options:
            answers[i] = {"opt": options[0], "custom": "", "ai_decided": False}
        else:
            answers[i] = {"opt": None, "custom": "(测试)开放回答", "ai_decided": False}
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
    st.session_state[f"_q_sat_{ridx}"] = 5
    if state.current_round()["condition"] == "E":
        st.session_state[f"_q_ai_q_quality_{ridx}"] = 6
    if ridx == 2:  # attention check round
        st.session_state[f"_q_attention_{ridx}"] = 2
    mine_label = t("q.tag_mine")  # shot widgets use localized labels as options
    parsed = shots.parse_shots(state.current_script())
    if parsed:
        for s in parsed:
            st.session_state[f"_q_shot{s['idx']}_{ridx}"] = mine_label
    else:
        st.session_state[f"_q_whole_{ridx}"] = mine_label
