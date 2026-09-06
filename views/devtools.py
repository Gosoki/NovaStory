"""Researcher-only manual-testing helpers (rendered inside the sidebar's
researcher section). Works because the sidebar renders BEFORE the main area:
assigning to a main-area widget key here happens before that widget is
instantiated in the same run, which Streamlit allows."""

from __future__ import annotations

import streamlit as st

from analysis import prereg
from core import db, llm, shots, state
from i18n import DEFAULT_LANG, get_lang, t
from views import questionnaire as _q

# Researcher one-click test inputs. Text is provided per session language and
# defaults to ja (the study language), so an injected test exercises the real
# Japanese pipeline; zh is for the researcher's own zh testing. TEST_INTENTS is
# keyed by the ja title (the topic's stable identity across languages).
TEST_INTENTS = {
    "拾った切符（誰かの落とし物）": {
        "ja": "落とし物の切符を拾った主人公。改札の向こうへ走り、息を切らして落とし主に手渡す。",
        "zh": "主角捡到一张别人掉的车票，追到检票口外，气喘吁吁地还给失主。",
        "en": "The hero picks up a dropped train ticket, chases past the gate, and hands it back out of breath.",
    },
    "最後のひと口（分け合う/独り占め）": {
        "ja": "最後のひと口を前に、主人公は「どうぞ」と差し出す。相手は一瞬ためらい、半分に割って返す。",
        "zh": "面对最后一口，主角说「你吃吧」递了过去；对方愣了一下，掰成两半还回来。",
        "en": "Facing the last bite, the hero offers it; the other hesitates, then breaks it in half and hands one back.",
    },
    "はじめての街の、最初の一歩": {
        "ja": "見知らぬ街に降り立った主人公。地図をしまい、匂いのするほうへ最初の一歩を踏み出す。",
        "zh": "主角刚到陌生的街，收起地图，朝着有香味的方向迈出第一步。",
        "en": "Just arrived in an unfamiliar town, the hero puts the map away and takes a first step toward a good smell.",
    },
}
_FALLBACK_INTENT = {
    "ja": "主人公は、ごく普通の一日の中で、どうにも説明のつかない小さな出来事に気づく。",
    "zh": "主角在最普通的一天里，发现了一件完全说不通的小事。",
    "en": "On an utterly ordinary day, the hero notices one small thing that makes no sense at all.",
}
EDIT_SNIPPET = {
    "ja": "\n（手直し：ラストで反転——主人公はこの一部始終を動画にして投稿し、バズる。）",
    "zh": "\n（我的修改：结局反转——主角把这一切拍成视频发到了网上，火了）",
    "en": "\n(My edit: twist ending — the hero films the whole thing, posts it, and it goes viral.)",
}
REVISION_SAMPLE = {
    "ja": "（テスト）全体をもっとコミカルに。最後のカットに小さなどんでん返しを足して。",
    "zh": "（测试）整体更搞笑一点，最后一镜加个反转",
    "en": "(test) Make the whole thing funnier and add a small twist in the last shot.",
}
_G_CUSTOM = {"ja": "（テスト）自分で書いた方向性", "zh": "（测试）我自己写的方向", "en": "(test) a direction I wrote myself"}
_G_OPEN = {"ja": "（テスト）自由回答", "zh": "（测试）开放回答", "en": "(test) free-text answer"}


def _session_text(d: dict) -> str:
    """Pick the session-language variant of a test string (default ja)."""
    return d.get(get_lang()) or d.get("ja") or ""


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
        "self_rating": 1, "script_confidence": 2, "aiexp_idx": 0, "trust": 4, "own_trait": 4,
        "quiz_correct": 0, "dev": True,
    }
    screening["is_novice"] = prereg.is_novice(screening)   # 别手写 True:novice 定义只在 prereg
    pid, seq, token = db.insert_participant(
        st.session_state.get("lang", DEFAULT_LANG), demographics, screening, passed=True
    )
    state.enter_rounds_directly(pid, seq, token)


def _switch_condition(cond: str) -> None:
    """Dev-only: swap the current round's condition and restart the round."""
    idx = st.session_state["round_idx"] - 1
    st.session_state["round_plan"][idx]["condition"] = cond
    state.reset_round_payload()
    # 同一轮重做:上一次填过的问卷答案不能残留到切换后的条件(这里在侧栏回调里跑,主区的
    # 问卷 widget 尚未挂载,删 key 是安全的;reset_round_payload 里不能做这件事)。
    for k in list(st.session_state.keys()):
        if k.startswith("_q_"):
            st.session_state.pop(k, None)
    state.log_event("dev_switch_condition", {"to": cond})
    # Fresh timing origin — r_events was wiped, so without a new round_start the
    # restarted round's t_read_intent / t_total would land NULL.
    state.log_event("round_start")


def _fill_current() -> None:
    phase = st.session_state["r_phase"]
    rd = state.current_round()
    cond = rd["condition"]
    if phase == "intent":
        # Key by the ja title (topic's stable identity); inject in the session
        # language so a ja test drives the real Japanese pipeline.
        key = state.topic_text(rd["topic"], "title", "ja")
        st.session_state["_intent_input"] = _session_text(TEST_INTENTS.get(key, _FALLBACK_INTENT))
    elif phase == "guidance":
        _fill_guidance()
    elif phase == "postgen":
        snippet = _session_text(EDIT_SNIPPET)
        cur = st.session_state.get("_script_edit") or state.current_script()
        if snippet.strip() not in cur:
            st.session_state["_script_edit"] = cur + snippet
        if cond == "D":
            st.session_state["_revision_input"] = _session_text(REVISION_SAMPLE)
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
            answers[i] = {"opt": None, "custom": _session_text(_G_CUSTOM), "ai_decided": False}
        elif i == 2 and options:
            answers[i] = {"opt": t("guidance.ai_decide"), "custom": "", "ai_decided": True}
        elif options:
            answers[i] = {"opt": options[0], "custom": "", "ai_decided": False}
        else:
            answers[i] = {"opt": None, "custom": _session_text(_G_OPEN), "ai_decided": False}
    st.session_state["r_g_answers"] = answers
    st.session_state["r_g_idx"] = len(qs) - 1


def _fill_questionnaire() -> None:
    ridx = st.session_state["round_idx"]
    # 题数从 questionnaire 取,别手抄:加一道 own4 之后一键填表会漏填,表现为「点了还是提交不了」
    for i in range(1, _q._OWN_ITEMS + 1):
        st.session_state[f"_q_own{i}_{ridx}"] = 5
    for i in range(1, _q._SOA_ITEMS + 1):
        st.session_state[f"_q_soa{i}_{ridx}"] = 4
    for i in range(1, _q._TLX_ITEMS + 1):
        st.session_state[f"_q_tlx{i}_{ridx}"] = 3
    st.session_state[f"_q_violation_{ridx}"] = 2
    st.session_state[f"_q_imagine_{ridx}"] = 6
    st.session_state[f"_q_sat_{ridx}"] = 5
    if state.current_round()["condition"] == "E":
        st.session_state[f"_q_ai_q_quality_{ridx}"] = 6
        st.session_state[f"_q_ai_q_amount_{ridx}"] = 4  # ai_q_best radio is optional
    if ridx == _q._ATTENTION_ROUND:
        st.session_state[f"_q_attention_{ridx}"] = _q._ATTENTION_EXPECTED
    mine_label = t("q.tag_mine")  # shot widgets use localized labels as options
    parsed = shots.parse_shots(state.current_script())
    if parsed:
        for s in parsed:
            st.session_state[f"_q_shot{s['idx']}_{ridx}"] = mine_label
    else:
        st.session_state[f"_q_whole_{ridx}"] = mine_label
