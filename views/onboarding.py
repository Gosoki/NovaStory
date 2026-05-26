from __future__ import annotations

import streamlit as st

from i18n import t


def render() -> None:
    st.header(t("onboarding.title"))
    st.caption(t("onboarding.hint"))

    topic = st.session_state["topic"]
    with st.container(border=True):
        st.markdown(f"**{t('onboarding.topic_preview')}**")
        st.markdown(f"### {topic['title']}")
        st.write(topic.get("scenario", ""))
        st.caption(
            t(
                "onboarding.topic_spec",
                count=topic["shot_count"],
                total=topic["total_seconds"],
            )
        )

    st.text_input(
        t("onboarding.subject_id_label"),
        key="_onb_subject_id_input",
        placeholder=t("sidebar.subject_id_placeholder"),
    )

    ready = bool((st.session_state.get("_onb_subject_id_input") or "").strip())
    st.caption(t("onboarding.flow_label"))
    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            t("sidebar.flow_abc"),
            type="primary",
            disabled=not ready,
            width="stretch",
            key="_begin_abc",
        ):
            _begin("ABC")
    with col2:
        if st.button(
            t("sidebar.flow_abd"),
            type="primary",
            disabled=not ready,
            width="stretch",
            key="_begin_abd",
        ):
            _begin("ABD")


def _begin(flow: str) -> None:
    # Snapshot widget value to a persistent (non-widget) key before the widget unmounts.
    # Without this, Streamlit's widget cleanup wipes `subject_id` the moment we leave onboarding.
    st.session_state["subject_id"] = (
        st.session_state.get("_onb_subject_id_input") or ""
    ).strip()
    st.session_state["flow"] = flow
    st.session_state["subject_started"] = True
    st.rerun()
