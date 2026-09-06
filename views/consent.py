from __future__ import annotations

import hashlib
from datetime import datetime

import streamlit as st

from core import state
from views import _scroll
from i18n import DEFAULT_LANG, t
from views._lang import language_radio


def proof_now() -> dict:
    """同意书版本存证(2026-09-01 拍板 0.5):被试**实际看到并勾选**的全部同意文字的指纹
    (正文 + 勾选项措辞 + 不刷新提示;只盖 body 的话改了 agree 那句事后照样说不清)+ 语言 + 时刻。
    采数期间同意书改一个字,事后就无法证明每位被试同意的是哪一版 —— 审查会直接问这个。"""
    sha1 = hashlib.sha1("\x1f".join(t(k) for k in ("consent.body", "consent.agree", "consent.no_refresh"))
                        .encode("utf-8")).hexdigest()[:16]
    return {"consent_sha1": sha1,
            "consent_lang": st.session_state.get("lang", DEFAULT_LANG),
            "consent_at": datetime.now().isoformat(timespec="seconds")}


def render() -> None:
    # 关掉标签页后重新扫码时 URL 里已经没有 ?t=,直接往下走就会 insert 第二行被试
    # 并吃掉一个拉丁方 seq(见 core/state 那段注释)。这台浏览器存过会话就给个入口。
    state.offer_resume(t("consent.resume_link"), t("consent.resume_hint"))
    state.log_intake_event("consent_shown")
    # Subject picks their language once, here, before consenting. After this it is
    # not shown to the subject anywhere (only admins can switch it) — keeps a
    # Japanese subject from ever flipping into another language mid-study (JP6).
    # All three languages are fully wired end-to-end (UI + LLM prompts + topics +
    # shot parser), so a subject gets a single-language session in whichever they
    # pick; ja is the default (langs[0]) for the formal Japanese cohort.
    language_radio("_consent_lang")
    st.divider()
    st.header(t("consent.title"))
    st.markdown(t("consent.body"))
    st.warning(t("consent.no_refresh"))
    agree = st.checkbox(t("consent.agree"), key="_consent_agree")
    if st.button(
        t("consent.start"),
        type="primary",
        disabled=not agree,
        width="stretch",
    ):
        # 指纹在**勾选同意的这一刻**抓、筛查提交时原样落库,而不是到筛查页再现算:
        # 它要证明的正是「这位被试同意的是哪一版」,抓取点离那一刻越近越站得住。
        st.session_state["_consent_proof"] = proof_now()
        state.log_intake_event("consent_agree", {"lang": st.session_state.get("lang")})
        # Background questionnaire first; the "how it works" briefing (flow +
        # input-freedom + storyboard sample) comes after it, so the briefing is
        # the last thing read before round 1 rather than being pushed out of
        # memory by a 13-item form (2026-09-01 §4).
        st.session_state["stage"] = "screening"
        _scroll.request()   # 同意 → 筛查:整页换内容
        st.rerun()
