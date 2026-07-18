from __future__ import annotations

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
