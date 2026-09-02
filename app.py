from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime

import streamlit as st

from core import config, db, state
from i18n import t
from views import (
    consent, final_survey, intro, researcher, round_common, screening, sidebar,
)


def main() -> None:
    st.set_page_config(
        page_title="NovaStory",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_css()
    state.init_state()
    sidebar.render()

    st.title(t("app.title"))
    st.caption(t("app.subtitle"))

    if st.session_state.get("researcher_mode") and st.session_state.get("researcher_ok"):
        researcher.render()
        return

    stage = st.session_state["stage"]
    if stage == "consent":
        consent.render()
    elif stage == "intro":
        intro.render()
    elif stage == "screening":
        screening.render()
    elif stage == "rounds":
        _progress_bar()
        st.divider()
        round_common.render()
    elif stage == "final_survey":
        final_survey.render()
    elif stage == "done":
        _done()


# Button color semantics:
#   green = decide / advance / finalize ("绿=决定")  ← all type="primary" buttons
#   blue  = keep iterating with the AI (a distinct, non-final action)  ← btn_more_ai
# Streamlit's default primary is coral/red, which first-time users misread as a
# dangerous action. Multiple selectors cover the 1.57 testid scheme plus a `kind`
# attribute fallback (non-matching selectors are harmless no-ops).
_ACTION_CSS = """
<style>
button[data-testid="stBaseButton-primary"],
button[data-testid="stBaseButton-primaryFormSubmit"],
.stButton button[kind="primary"],
.stFormSubmitButton button[kind="primary"] {
    background-color: #16a34a !important;
    border-color: #16a34a !important;
    color: #ffffff !important;
}
button[data-testid="stBaseButton-primary"]:hover,
button[data-testid="stBaseButton-primaryFormSubmit"]:hover,
.stButton button[kind="primary"]:hover,
.stFormSubmitButton button[kind="primary"]:hover {
    background-color: #15803d !important;
    border-color: #15803d !important;
}
div.st-key-btn_more_ai button {
    background-color: #2563eb !important;
    border: 1px solid #2563eb !important;
    color: #ffffff !important;
}
div.st-key-btn_more_ai button:hover {
    background-color: #1d4ed8 !important;
    border-color: #1d4ed8 !important;
}
/* Streamlit 1.60's fixed top header (position:absolute, height 3.75rem) renders
   transparent and click-through BY ITSELF only while it has nothing to show.
   A collapsed sidebar puts the expand chevron in it, which flips it to an opaque
   full-width bar that page content then scrolls underneath — "a black bar
   covering my content". Force it transparent and click-through.

   stToolbar carries its own `pointer-events: auto` and spans the full width, so
   `pointer-events:none` on the header alone is CANCELLED by it and the top 60px
   strip stays click-dead — now invisibly, which is worse than the bar. Kill it on
   the toolbar too and re-enable only the two real controls. */
header[data-testid="stHeader"],
header[data-testid="stHeader"] [data-testid="stToolbar"] {
    background: transparent !important;
    pointer-events: none !important;
}
header[data-testid="stHeader"] [data-testid="stExpandSidebarButton"],
header[data-testid="stHeader"] [data-testid="stStatusWidget"],
header[data-testid="stHeader"] [data-testid="stMainMenu"] {
    pointer-events: auto !important;
}
/* Accent + de-emphasis, split by theme. config.toml defines BOTH [theme.light]
   and [theme.dark], so the app follows the participant's OS setting and
   toolbarMode="minimal" hides the menu that would let them override it — the
   light theme is the default for anyone whose phone is in light mode, not a
   corner case. One shared pair of values would fail WCAG AA on one of the two
   (#3b82f6 is 3.7:1 on white; #2563eb is 3.1:1 on Streamlit's dark #0E1117). */
:root { --ns-accent: #2563eb; --ns-dim: #6e6e6e; }
@media (prefers-color-scheme: dark) {
    :root { --ns-accent: #3b82f6; --ns-dim: #a3a3a3; }
}
</style>
"""


def _inject_css() -> None:
    st.markdown(_ACTION_CSS, unsafe_allow_html=True)


def _progress_bar() -> None:
    i = st.session_state["round_idx"]
    st.progress(
        (i - 1) / config.N_ROUNDS,
        text=t("round.progress", i=i, n=config.N_ROUNDS),
    )


# Deliberately permissive: one @, a dot in the domain, no whitespace. A stricter
# pattern rejects real addresses (new TLDs, +tags, unicode locals) and the only
# cost of a typo here is one undeliverable mail — this must never be the reason a
# finished participant can't leave their address.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")


def _done() -> None:
    if not st.session_state.get("completion_code"):
        st.session_state["completion_code"] = db.make_completion_code(
            st.session_state["participant_id"]
        )
        # 只有「在本次会话里真的做完了」才置位。续接一位已完成的被试时完成码是从库里
        # 读回来的,不会走这里 —— 这正是联系方式表单的归属凭据。
        st.session_state["_own_completion"] = True
    st.success(t("done.title"))
    st.write(t("done.message"))
    st.code(st.session_state["completion_code"])
    st.caption(t("done.code_hint"))
    st.warning(t("done.no_repeat"))
    _contact_form()
    if st.session_state.get("researcher_ok"):
        if st.button(t("done.reset"), type="primary"):
            state.reset_for_next()
            st.rerun()
        st.caption(t("done.reset_hint"))


def _contact_form() -> None:
    """Opt-in contact capture on the completion screen (2026-09-01 §13).

    Kept strictly AFTER every measurement is in the database: it writes to the
    participant row via `contact_json` and touches nothing the analysis reads, so
    a subject who leaves an address is not a different data point from one who
    doesn't. Opt-in and optional — the wording says the film is AI-generated and
    may take up to a year, so nobody leaves an address on a wrong expectation.

    ⚠️ 这是全库唯一一列直接个人数据,与同意书「匿名分析」的口径冲突尚未处理
    (docs/paper/13 §0.7)。"""
    pid = st.session_state.get("participant_id")
    if not pid:
        return
    with st.container(border=True):
        st.subheader(t("done.contact_title"))
        st.markdown(t("done.contact_body"))
        # 「存过没有」以数据库为准,不以 session_state 为准:重连会丢掉 session,
        # 于是被试会看到一张空表单、以为没存上,再填一次就把上一次连同备注一起顶掉。
        row = db.get_participant(pid) or {}
        if row.get("contact_json"):
            st.success(t("done.contact_saved"))
            return
        if not st.session_state.get("_own_completion"):
            # 拿着别人转发/共用机器上残留的 ?t= 网址进来的人,不给写。
            st.caption(t("done.contact_closed"))
            return
        want = st.checkbox(t("done.contact_want"), key="_contact_want")
        email = st.text_input(t("done.contact_email"), key="_contact_email")
        note = st.text_area(
            t("done.contact_note"), key="_contact_note",
            placeholder=t("done.contact_note_ph"), height=80,
        )
        st.caption(t("done.contact_privacy"))
        if st.button(t("done.contact_submit"), width="stretch"):
            # NFKC:日本語IMEが全角のままだと「ｔａｒｏ＠ｅｘａｍｐｌｅ．ｃｏｍ」になり、
            # 半角に直さないと弾かれる(しかも半端に変換された全角ドメインは通ってしまい、
            # 1年後に不達で気づく)。
            addr = unicodedata.normalize("NFKC", email or "").strip()
            if not _EMAIL_RE.match(addr):
                st.error(t("errors.email_bad"))
                return
            payload = {
                "email": addr,
                "want_video": bool(want),
                "note": unicodedata.normalize("NFKC", note or "").strip(),
                "at": datetime.now().isoformat(timespec="seconds"),
                # 与 screening 的 consent_sha1 同一套存证:被试交出邮箱时**看到的是哪一版
                # 说明**,事后必须能答得上来(说明文一改,旧版本就在世上不存在了)。
                "disclosure_sha1": hashlib.sha1(
                    "\x1f".join(t(k) for k in
                                ("done.contact_body", "done.contact_want",
                                 "done.contact_privacy", "done.no_repeat")
                                ).encode("utf-8")).hexdigest()[:16],
                "lang": st.session_state.get("lang", "ja"),
            }
            try:
                stored = db.set_contact(pid, json.dumps(payload, ensure_ascii=False))
            except Exception:  # noqa: BLE001 — 库锁/磁盘满都不该在完成页上抛栈
                st.error(t("errors.contact_failed"))
                return
            if stored:
                # round_idx=0(intake 段的约定):这件事发生在所有轮次之外,记进第 3 轮的
                # 事件流会让「本轮最后一个事件」延伸到被试敲完邮箱为止。
                try:
                    db.insert_event(pid, 0, "contact_submit", {"want_video": bool(want)},
                                    attempt=st.session_state.get("session_id") or None)
                except Exception:  # noqa: BLE001 — 埋点失败不该拦住被试
                    pass
            st.rerun()


if __name__ == "__main__":
    main()
