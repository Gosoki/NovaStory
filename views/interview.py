from __future__ import annotations

import html
import re

import streamlit as st

from core import config, prompts, state
from i18n import get_lang, t
from views import _postgen
from views._streaming import RETRY, call_llm_json, stream_llm

# Condition F —「想いを聞く」インタビュー(2026-10-27,docs/paper/06 ㉖)。
#
# 与 E 的区别是「问什么、怎么问」:E 一次给约 6 道选择题,问的是作品怎么拍(动机 / 转折 /
# 关键画面 + AI 补充);F 一题一题地问作者自己对作品的想法,只能自己写。共 3-5 题
# (config.INTERVIEW_RANGE):前 3 题的观点固定 ——
#   1. liked             这个创意最喜欢哪里          —— 固定题面,不调 AI
#   2. audience_feeling  想让观众有什么感受          —— AI 按上一个回答调整措辞
#   3. moment            那份感受在哪一瞬间最能传达  —— 同上
# 第 3 题之后由 AI 判断还要不要追问(follow_up,最多再 2 题;回答短 / 模糊时追问,说清楚了就停),
# 与 E「3 个固定观点 + AI 补 2-4 个」同一思路。问完后显示致谢 + 「生成」按钮。
# 每题前的复述句(「『…』ということですね」)由这里的模板生成、只引用被试原文 —— 不让模型
# 写,免得夸奖 / 共情 / 改写混进来(那会让「AI 亲切」冒充「自我投入」)。
# 计时与 E 共用事件对 guidance_shown → guidance_submit(state.round_durations);问答存成
# guidance_json 的一轮(source="interview"),对话历史与问卷的「最有帮助的问题」直接复用。

_DIMS = ("liked", "audience_feeling", "moment")
_CLIP = 40
_TAIL = re.compile(r"[。．.!！?？\s]+$")
# 模型偶尔会无视「不给例子」—— 带这些标记的题面不用,退回固定题面。
_EXAMPLE_MARKERS = ("例えば", "たとえば", "例:", "例：", "など", "比如", "例如", "之类",
                    "for example", "e.g.", "such as")
# ja 题面的额外规则(2026-10-29 审计:合成题测试里这些都漏网了)。F 只在 ja 的 4 条件设计里出现。
#   选项 / 举例:「それとも」「どちら」「とか」「みたいな」、「のような」(「どのような」除外)
#   拍摄技术:提示里禁止问,题面里出现就不用
_JA_CHOICE = re.compile(r"それとも|または|どちら|とか|みたいな|(?<!ど)のような")
_JA_CAMERA = re.compile(r"カメラ|アングル|アップ|撮影|撮り方|秒数")
_JA_KANA = re.compile(r"[ぁ-ゖァ-ヺ]")
_MID_STOP = re.compile(r"[。．.!！?？]\s*\S")   # 句末符号后面还有字 = 不止一句(先夸奖 / 复述再问)
_PAIRED_QUOTES = ("「」", "『』", "“”", '""', "''")
_JA_MAX = 80   # 提示要求 60 字以内,留一点余量


def begin() -> None:
    st.session_state["r_iv_turns"] = []
    st.session_state["r_iv_done"] = False
    for k in list(st.session_state.keys()):
        if k.startswith("_iv_"):
            st.session_state.pop(k, None)
    st.session_state["r_phase"] = "interview"


def _clip(text: str) -> str:
    s = _TAIL.sub("", (text or "").strip()).replace("\n", " ")
    return s if len(s) <= _CLIP else s[:_CLIP] + "…"


def _fixed_question(i: int) -> str:
    return t(f"interview.q{i + 1}")


def _first_turn() -> dict:
    return {"dimension": _DIMS[0],
            "echo": t("interview.echo_intent", a=_clip(st.session_state["r_intent"])),
            "question": _fixed_question(0), "answer": None, "fallback": False}


def _clean_question(data) -> str | None:
    if not isinstance(data, dict):
        return None
    q = data.get("question")
    if not isinstance(q, str):
        return None
    q = q.strip()
    # 只剥成对的外层引号:「最後のひと口」の場面で…？ 这种开头引用原话的题,首尾不是一对,不能剥
    if len(q) >= 2 and q[0] + q[-1] in _PAIRED_QUOTES:
        q = q[1:-1].strip()
    if not (4 <= len(q) <= 200) or "\n" in q:   # 200:en 的 25 词问题也放得下
        return None
    low = q.lower()
    if any(m in low for m in _EXAMPLE_MARKERS) or _MID_STOP.search(q):
        return None
    if get_lang() == "ja" and (len(q) > _JA_MAX or not _JA_KANA.search(q)
                               or _JA_CHOICE.search(q) or _JA_CAMERA.search(q)):
        return None
    return q


def _fixed_turn(i: int) -> dict:
    """第 i 题的固定题面版本(模型给不出可用题面 / 网关连续失败时用)。"""
    turns = st.session_state["r_iv_turns"]
    return {"dimension": _DIMS[i], "echo": t("interview.echo", a=_clip(turns[-1]["answer"])),
            "question": _fixed_question(i), "answer": None, "fallback": True}


def _next_turn(topic: dict, i: int) -> dict | object:
    """第 i 题(i ≥ 1):AI 按上一个回答调整措辞;JSON 不可用时退回固定题面。RETRY = 临时失败。"""
    turns = st.session_state["r_iv_turns"]
    lang = get_lang()
    data = call_llm_json(
        prompts.build_system_interview(lang),
        prompts.build_user_interview(topic, st.session_state["r_intent"], turns, _DIMS[i], lang),
        group=f"F-interview-q{i + 1}",
    )
    if data is RETRY:
        return RETRY
    q = _clean_question(data)
    if q is None:
        return _fixed_turn(i)
    return {"dimension": _DIMS[i], "echo": t("interview.echo", a=_clip(turns[-1]["answer"])),
            "question": q, "answer": None, "fallback": False}


def _more_turn(topic: dict) -> dict | None | object:
    """第 3 题之后:AI 判断要不要追问。返回下一题;None = 不再问;RETRY = 临时失败。"""
    turns = st.session_state["r_iv_turns"]
    lang = get_lang()
    data = call_llm_json(
        prompts.build_system_interview_more(lang),
        prompts.build_user_interview_more(topic, st.session_state["r_intent"], turns, lang),
        group=f"F-interview-more{len(turns) + 1}",
    )
    if data is RETRY:
        return RETRY
    if not (isinstance(data, dict) and data.get("more") is True):
        return None   # 模型说不用再问(或 JSON 不可用):采访到此结束
    q = _clean_question(data)
    if q is None:
        return None   # 想追问但题面不合要求(带例子 / 太长):宁可不问
    return {"dimension": "follow_up", "echo": t("interview.echo", a=_clip(turns[-1]["answer"])),
            "question": q, "answer": None, "fallback": False}


def render(topic: dict) -> None:
    turns = st.session_state["r_iv_turns"]
    lo, hi = config.INTERVIEW_RANGE
    if not turns:
        turns.append(_first_turn())
        state.log_event("guidance_shown", {"round": 1, "source": "interview",
                                           "n_questions": f"{lo}-{hi}", "fallback": False},
                        once=True)
    elif turns[-1]["answer"] is not None and not st.session_state["r_iv_done"]:
        if len(turns) >= hi:
            st.session_state["r_iv_done"] = True
            st.rerun()
        _render_chat(turns)   # 等下一题时,已答的对话留在屏上(进度条出现在它下面)
        if not st.session_state.get("_iv_gen_failed"):
            fixed_dim = len(turns) < lo
            nxt = _next_turn(topic, len(turns)) if fixed_dim else _more_turn(topic)
            if nxt is RETRY:
                # 网关连续失败两次就不再等:固定观点退回固定题面,追问则到此为止 —— 不让被试卡在重试按钮上
                st.session_state["_iv_fail_n"] = st.session_state.get("_iv_fail_n", 0) + 1
                if st.session_state["_iv_fail_n"] >= 2:
                    nxt = _fixed_turn(len(turns)) if fixed_dim else None
            if nxt is not RETRY:
                st.session_state.pop("_iv_fail_n", None)
                if nxt is None:
                    st.session_state["r_iv_done"] = True
                else:
                    turns.append(nxt)
                st.rerun()
        # 临时失败:明确的重试按钮(同 E 的出题页),不让被试卡死在这一屏。
        st.session_state["_iv_gen_failed"] = True
        if st.button(t("round.retry"), type="primary", width="stretch"):
            st.session_state.pop("_iv_gen_failed", None)
            state.log_event("retry_click", {"group": "F-interview"})
            st.rerun()
        return

    _render_chat(turns)
    if st.session_state["r_iv_done"]:
        # 问完了:致谢 + 生成按钮。生成被 rerun 打断(双击、手机休眠后重连)或失败时,回答都已存好,
        # 下一次渲染仍是这里 —— 再按一次就重新生成,不会卡在没有按钮的空页。
        st.info(t("interview.thanks"))
        if st.button(t("interview.finish"), type="primary", width="stretch"):
            _finish(topic)
        return
    cur = len(turns) - 1
    key = f"_iv_answer_{cur}"
    st.text_area(t("interview.answer_label"), key=key, height=90, max_chars=300)
    if st.button(t("interview.send"), type="primary", width="stretch"):
        answer = (st.session_state.get(key) or "").strip()
        if not answer:
            st.error(t("interview.need_answer"))
            return
        state.log_event("guidance_answer_saved", {"round": 1, "q": cur})
        turns[cur]["answer"] = answer
        st.rerun()


def _render_chat(turns: list[dict]) -> None:
    st.subheader(t("interview.title"))
    if not st.session_state["r_iv_done"]:
        lo, hi = config.INTERVIEW_RANGE
        st.caption(t("interview.progress", i=len(turns), lo=lo, hi=hi))
    for x in turns:
        with st.chat_message("assistant"):
            st.markdown(
                f"<div style='color:var(--ns-dim,#6e6e6e);font-size:.92em;margin-bottom:.15rem'>"
                f"{html.escape(x['echo'])}</div>",
                unsafe_allow_html=True,
            )
            st.markdown(f"**{x['question']}**")
        if x["answer"] is not None:
            with st.chat_message("user"):
                st.markdown(_postgen._pre(x["answer"]), unsafe_allow_html=True)


def _finish(topic: dict) -> None:
    turns = st.session_state["r_iv_turns"]
    items = [{
        "dimension": x["dimension"], "question": x["question"], "echo": x["echo"],
        "options": [], "chosen": x["answer"], "picked_option": None,
        "is_custom": True, "ai_decided": False, "fallback": bool(x.get("fallback")),
    } for x in turns]
    lang = get_lang()
    out = stream_llm(
        prompts.build_system_script(topic, lang),
        prompts.build_user_script_from_answers(topic, st.session_state["r_intent"], items, lang),
        group="F-final",
    )
    # 生成成功才提交这一轮(同 E 的 _finish_round):失败后重试不会重复记一轮问答。
    if not (out and out.strip()):
        return   # 仍停在「问完了」这一屏,按钮再按一次就重新生成
    for it in items:
        state.log_event("guidance_answer", {"dimension": it["dimension"], "is_custom": True,
                                            "ai_decided": False})
    st.session_state["r_guidance_rounds"].append({"round": 1, "source": "interview", "items": items})
    state.log_event("guidance_submit", {"round": 1})
    state.add_version(out, "ai")
    st.session_state["r_phase"] = "postgen"
    _postgen.request_editor_refresh()
    st.rerun()
