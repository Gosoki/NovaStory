from __future__ import annotations

import hmac
import time

import streamlit as st

from core import config, state
from i18n import t
from views import devtools
from views._lang import language_radio


def _admin_requested() -> bool:
    """研究员入口只在网址带 ?admin=1 时渲染。以前每位被试都能看到「管理者ツール」和一个
    无限速的口令框 —— 口令强度是唯一防线,而且被试会困惑那是什么。"""
    try:
        return (st.query_params.get("admin") or "") == "1"
    except Exception:  # noqa: BLE001 — headless AppTest 等无 query_params 的场景
        return False


# 按来源 IP 的失败计数(进程级):5 次失败锁 60 秒。同一会话内另有指数退避;两者叠加,
# 「开很多会话猜口令」也从每秒多次变成一分钟五次。真正的防线仍是反代 + 强口令(deploy_check)。
_PW_FAILS: dict[str, tuple[int, float]] = {}
_PW_LOCK_AFTER, _PW_LOCK_SECONDS = 5, 60.0


def _client_key() -> str:
    try:
        ip = getattr(st.context, "ip_address", None)
    except Exception:  # noqa: BLE001
        ip = None
    return str(ip or st.session_state.get("session_id") or "?")


def render() -> None:
    if not (st.session_state.get("researcher_ok") or _admin_requested()):
        return
    with st.sidebar:
        _researcher_section()


def _language_picker() -> None:
    # Admin-only now (subjects set their language once on the consent page, JP6).
    # Admins can switch any time for testing.
    st.subheader(t("sidebar.language"))
    language_radio("_admin_lang", collapsed=True)


def _researcher_section() -> None:
    # Stay open once unlocked, so an in-panel rerun (e.g. switching language)
    # doesn't snap the panel shut on the researcher.
    expanded = bool(st.session_state.get("researcher_ok"))
    with st.expander(t("sidebar.researcher_section"), expanded=expanded):
        if not st.session_state.get("researcher_ok"):
            pw = st.text_input(
                t("sidebar.researcher_pw"), type="password", key="_researcher_pw"
            )
            if st.button(t("sidebar.researcher_unlock"), width="stretch"):
                # 常数时间比较 + 按失败次数指数退避(同一会话内)。这挡不住开很多会话的
                # 暴力尝试 —— 真正的防线是反代/HTTPS/强口令(deploy_check)——但让「猜口令」
                # 从每秒多次变成几秒一次,成本一行。
                fails = int(st.session_state.get("_pw_fails", 0))
                key = _client_key()
                n_ip, until = _PW_FAILS.get(key, (0, 0.0))
                expected = config.researcher_password()
                if not expected:
                    st.error(t("sidebar.researcher_pw_unset"))   # fail-closed:没配口令就没有后台
                elif time.time() < until:
                    st.error(t("sidebar.researcher_pw_locked"))
                elif hmac.compare_digest((pw or "").encode(), expected.encode()):
                    st.session_state["researcher_ok"] = True
                    st.session_state["_pw_fails"] = 0
                    _PW_FAILS.pop(key, None)
                    st.rerun()
                else:
                    st.session_state["_pw_fails"] = fails + 1
                    n_ip += 1
                    _PW_FAILS[key] = (n_ip, time.time() + _PW_LOCK_SECONDS if n_ip >= _PW_LOCK_AFTER else 0.0)
                    if n_ip >= _PW_LOCK_AFTER:
                        _PW_FAILS[key] = (0, time.time() + _PW_LOCK_SECONDS)
                    time.sleep(min(2 ** fails, 8))
                    st.error(t("sidebar.researcher_pw_wrong"))
            return

        _language_picker()
        st.toggle(t("sidebar.researcher_toggle"), key="researcher_mode")
        devtools.render()
        _api_section()
        _topics_preview()
        if st.button(t("sidebar.reset_subject"), width="stretch"):
            state.reset_for_next()
            st.rerun()
        # 按钮名叫「初始化所有记录」,但 reset_for_next 只清当前浏览器会话 —— 说清楚,
        # 否则采数前用它「清掉试测数据」的人会以为库空了,而试测行还在监控面板的
        # 完成数、novice 占比、seq 平衡格里跟真被试混在一起。
        st.caption(t("sidebar.reset_hint"))
        if st.button(t("sidebar.researcher_logout"), width="stretch"):
            st.session_state["researcher_ok"] = False
            st.session_state["researcher_mode"] = False
            st.rerun()


def _api_section() -> None:
    st.subheader(t("sidebar.api_section"))
    cfgs = state.load_api_configs()
    if not cfgs:
        st.warning(t("sidebar.api_no_configs"))
        return

    names = [c.get("name", f"#{i}") for i, c in enumerate(cfgs)]
    if "_api_preset_idx" not in st.session_state:
        st.session_state["_api_preset_idx"] = 0
    if st.session_state["_api_preset_idx"] >= len(cfgs):
        st.session_state["_api_preset_idx"] = 0

    idx = st.selectbox(
        t("sidebar.api_preset_label"),
        options=list(range(len(cfgs))),
        format_func=lambda i: names[i],
        key="_api_preset_idx",
    )
    chosen = cfgs[idx]
    # Push into the session keys that core/llm.py reads.
    st.session_state["base_url"] = chosen["base_url"]
    st.session_state["model"] = chosen["model"]
    st.session_state["api_key"] = chosen["api_key"]
    st.session_state["api_preset_name"] = chosen.get("name", "")

    st.caption(t("sidebar.api_active", name=chosen.get("name", "—")))
    if not chosen["api_key"]:
        st.warning(t("sidebar.api_key_missing"))


def _topics_preview() -> None:
    st.subheader(t("sidebar.topic_section"))
    st.caption(t("sidebar.topics_hint"))
    topics = state.load_topics()
    st.json(topics, expanded=False)
