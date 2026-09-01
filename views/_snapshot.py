from __future__ import annotations

import streamlit as st

from core import state
from i18n import t

# 事前意图快照(2026-09-01 拍板 2.5)——保真度的参照点。
#
# 要解决的问题:`q.imagine`(「终稿和你想象的画面有多接近」)在 E 条件下**参照系已被 E 自己
# 塑造过** —— AI 先问了 5-7 个问题,被试的「想象」在回答过程中被重新塑造,答题时拿来比对的
# 已经不是他进来时的那个想象。于是「E 保真最高」有一部分是测量假象,而不是效应。
#
# 做法:在**任何 AI 介入之前**、三个条件**完全同文**地让被试写下 3 条「一定要有的东西」。
# 它是**测量工具而不是处理**:同一屏、同一措辞、同一时点出现在 C/D/E,所以不会差异化地
# 影响某个条件,却给保真度一个 AI 碰不到的锚。问卷阶段把这三条原样显示在量表旁边,
# 被试比对的是自己写下的字,不是回忆。
#
# 刻意不做的:不给选项、不给维度、不给示例句式 —— 一旦给了就成了 E 式的结构化引导,
# 会污染 C/E 对比。只有三个空行。

N_ITEMS = 3
MIN_FILLED = 1   # 至少写一条才有锚;全空则保真度无参照,与「客观下界」口径不符


def render(topic: dict) -> None:
    st.info(t("snapshot.lead"))
    st.caption(t("snapshot.why"))
    for i in range(N_ITEMS):
        st.text_input(
            t("snapshot.item", i=i + 1),
            key=f"_snap_{i}",
            placeholder=t("snapshot.placeholder") if i == 0 else "",
        )
    st.caption(t("snapshot.optional", n=MIN_FILLED))

    if st.button(t("snapshot.submit"), type="primary", width="stretch"):
        items = [(st.session_state.get(f"_snap_{i}") or "").strip() for i in range(N_ITEMS)]
        filled = [x for x in items if x]
        if len(filled) < MIN_FILLED:
            st.error(t("errors.snapshot_empty", n=MIN_FILLED))
            return
        st.session_state["r_snapshot"] = filled
        state.log_event("snapshot_submit",
                        {"n_items": len(filled),
                         "chars": sum(len(x) for x in filled)})
        st.session_state["r_phase"] = "pipeline"
        st.rerun()


def render_reference() -> None:
    """问卷里把快照原样显示出来 —— 让被试比对**自己写下的字**,而不是回忆。"""
    snap = st.session_state.get("r_snapshot") or []
    if not snap:
        return
    with st.container(border=True):
        st.caption(t("snapshot.recall"))
        for x in snap:
            st.markdown(f"- {x}")
