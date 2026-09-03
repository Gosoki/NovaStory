from __future__ import annotations

import streamlit as st

from core import prompts, state
from i18n import get_lang, t
from views._streaming import stream_llm


def render_first_generation(topic: dict, call_tag: str) -> None:
    """C / D 共用的首次生成:创意 → 整稿(流式)。两者只差 LLM 调用点标签;C/D 的区别
    在之后 —— C 进只读页,D 进可编辑循环(views/round_common.py)。

    成功 → postgen;失败/空流 → stream_llm 已报错,这里给一颗重试按钮,被试永远不会
    卡在一个没有任何可点控件的页面上。"""
    if st.session_state.get("_gen_failed"):
        # 上一次生成失败:只亮重试按钮,**不要**在同一次 rerun 里再生成 —— 以前按钮判定排在
        # 生成之后,点一下重试 = 先跑一遍生成、再因按钮为真 rerun 又跑一遍(各含 3 次内部重试)。
        if st.button(t("round.retry"), type="primary", width="stretch"):
            st.session_state.pop("_gen_failed", None)
            state.log_event("retry_click", {"group": call_tag})
            st.rerun()
        return
    lang = get_lang()
    out = stream_llm(
        prompts.build_system_script(topic, lang),
        prompts.build_user_script(topic, st.session_state["r_intent"], lang),
        group=call_tag,
    )
    if out and out.strip():
        state.add_version(out, "ai")
        st.session_state["r_phase"] = "postgen"
        st.rerun()
    else:
        st.session_state["_gen_failed"] = True
        if st.button(t("round.retry"), type="primary", width="stretch"):
            st.session_state.pop("_gen_failed", None)
            state.log_event("retry_click", {"group": call_tag})
            st.rerun()
