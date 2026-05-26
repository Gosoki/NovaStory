from __future__ import annotations

import streamlit as st

from core import state
from i18n import t
from views import group_a, group_b, group_c, group_d, onboarding, researcher, sidebar


def main() -> None:
    st.set_page_config(
        page_title="NovaStory",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    state.init_state()
    sidebar.render()

    st.title(t("app.title"))
    st.caption(t("app.subtitle"))

    if st.session_state.get("researcher_mode"):
        researcher.render()
        return

    if not _preflight_ok():
        return

    if not st.session_state.get("subject_started"):
        onboarding.render()
        return

    _status_bar()
    st.divider()

    if state.is_done():
        _render_done()
        return

    step = st.session_state["step"]
    if step == "A":
        group_a.render()
    elif step == "B":
        if state.can_view("B"):
            group_b.render()
        else:
            st.warning(t("errors.locked_prev_step"))
    elif step == "C":
        if state.can_view("C"):
            group_c.render()
        else:
            st.warning(t("errors.locked_prev_step"))
    elif step == "D":
        if state.can_view("D"):
            group_d.render()
        else:
            st.warning(t("errors.locked_prev_step"))


def _preflight_ok() -> bool:
    """Researcher-side guardrail: topic must be configured before any subject can start."""
    topic = st.session_state.get("topic", {})
    if not (topic.get("title") or "").strip() or not (topic.get("scenario") or "").strip():
        st.warning(t("errors.topic_required"))
        return False
    return True


def _status_bar() -> None:
    topic = st.session_state["topic"]
    flow = st.session_state["flow"]
    step = st.session_state["step"]

    cols = st.columns(4)
    cols[0].metric(t("status.subject_label"), st.session_state["subject_id"])
    cols[1].metric(t("status.flow_label"), flow)
    cols[2].metric(t("status.topic_label"), topic.get("title", ""))

    step_labels = {
        "A": t("status.group_a"),
        "B": t("status.group_b"),
        "C": t("status.group_c"),
        "D": t("status.group_d"),
        "DONE": t("status.done"),
    }
    cols[3].metric(t("status.step_label"), step_labels.get(step, step))

    # Progress: A=0.33, B=0.66, C/D=1.0
    progress_map = {"A": 0.0, "B": 0.34, "C": 0.67, "D": 0.67, "DONE": 1.0}
    st.progress(progress_map.get(step, 0.0))


def _render_done() -> None:
    st.success(t("done.title"))
    st.write(t("done.message"))
    if st.button(t("done.reset"), type="primary"):
        state.reset_subject()
        st.rerun()


if __name__ == "__main__":
    main()
