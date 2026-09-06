"""把页面滚回顶部 —— 各阶段共用。

Streamlit 是单页应用,`st.rerun()` **不会**重置滚动位置。所以每次「提交 → 进入下一步」,
被试都还停在刚才那颗按钮所在的高度,而新页面的开头在视口上方看不见 ——
被试端的表现就是「点了没反应」。

这套逻辑本来只长在问卷页里(只有 提交脚本 → 问卷 那一跳会滚),其余每一跳都没有,
所以提到这里,由 `app.main` 统一消费。

用法:推进流程的按钮在 `st.rerun()` 之前调 `request()`,`app.main` 每次渲染调一次
`apply_pending()`。**只能有一个消费点** —— 标志是 pop 出来的,谁先读到谁生效。
"""

from __future__ import annotations

import time

import streamlit as st

# 键名与 core/state.py、views/_trial.py 里直接置位的那处保持一致,别改。
_FLAG = "_scroll_top"


def request() -> None:
    """请求下一次渲染时滚回顶部。在 `st.rerun()` 之前调用。"""
    st.session_state[_FLAG] = True


def apply_pending() -> None:
    """有待处理的请求就注入滚动脚本。在页面主体渲染前调用一次。"""
    if st.session_state.pop(_FLAG, False):
        _emit()


def _emit() -> None:
    """滚的是 `.stMain` —— Streamlit 的**内层**滚动容器,window 本身通常不滚
    (问卷页那个分镜表观察器也是为此才监听 capture 阶段的 scroll)。

    分镜表、配图与 2 秒轮询的 fragment 会在随后几帧里改变页面高度,所以补两次延时调用,
    免得刚滚上去又被撑回中间。nonce 让每次注入的 script 内容都不同,
    否则 Streamlit 会复用旧节点、跳过执行。

    用 `st.html` 而不是 `st.components.v1.html`:后者在 1.57 起的 iframe 带 `sandbox`
    但不带 `allow-same-origin`,拿不到 `window.parent.document`。
    """
    st.html(
        "<script>(function(){var n=%d;" % int(time.time() * 1000)
        + "var go=function(){var d=(window.parent&&window.parent.document)||document;"
        + "var m=d.querySelector('.stMain')||d.querySelector('[data-testid=\"stMain\"]');"
        + "if(m){m.scrollTo(0,0);}(window.parent||window).scrollTo(0,0);};"
        + "go();requestAnimationFrame(go);setTimeout(go,80);setTimeout(go,300);})();</script>",
        unsafe_allow_javascript=True,
    )
