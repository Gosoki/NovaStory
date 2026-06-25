from __future__ import annotations

import streamlit as st

from core import config, db, state
from i18n import t
from views import consent, researcher, round_common, screening, sidebar


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
    elif stage == "screening":
        screening.render()
    elif stage == "rounds":
        _progress_bar()
        st.divider()
        round_common.render()
    elif stage == "done":
        _done()


# "Keep iterating with the AI" buttons are tinted blue so they read as a
# distinct, non-final action; the finalize/submit button keeps Streamlit's
# default primary color. Opt-in via key (Streamlit adds a `st-key-<key>` class).
_ACTION_CSS = """
<style>
div.st-key-btn_more_ai button {
    background-color: #2563eb !important;
    border: 1px solid #2563eb !important;
    color: #ffffff !important;
}
div.st-key-btn_more_ai button:hover {
    background-color: #1d4ed8 !important;
    border-color: #1d4ed8 !important;
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


def _done() -> None:
    if not st.session_state.get("completion_code"):
        st.session_state["completion_code"] = db.make_completion_code(
            st.session_state["participant_id"]
        )
    st.success(t("done.title"))
    st.write(t("done.message"))
    st.code(st.session_state["completion_code"])
    st.caption(t("done.code_hint"))
    if st.session_state.get("researcher_ok"):
        if st.button(t("done.reset"), type="primary"):
            state.reset_for_next()
            st.rerun()


if __name__ == "__main__":
    main()
