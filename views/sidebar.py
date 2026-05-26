from __future__ import annotations

import streamlit as st

from core import state
from i18n import AVAILABLE_LANGS, LANG_LABELS, t


TOPIC_FIELDS = [
    ("title", "_topic_title", str),
    ("scenario", "_topic_scenario", str),
    ("shot_count", "_topic_shots", int),
    ("total_seconds", "_topic_total", int),
]


def render() -> None:
    _sync_topic_to_widget_state()

    with st.sidebar:
        _language_picker()
        st.divider()
        _api_section()
        st.divider()
        _topic_section()
        st.divider()
        _researcher_toggle()

    _sync_widget_state_to_topic()


# ---------- topic <-> widget state sync (avoids Streamlit value+key warnings) ----------

def _sync_topic_to_widget_state() -> None:
    topic = st.session_state["topic"]
    for src_key, widget_key, cast in TOPIC_FIELDS:
        if widget_key not in st.session_state:
            st.session_state[widget_key] = cast(topic.get(src_key, "" if cast is str else 1))


def _sync_widget_state_to_topic() -> None:
    if st.session_state.get("topic_frozen"):
        return
    topic = st.session_state["topic"]
    for src_key, widget_key, cast in TOPIC_FIELDS:
        if widget_key in st.session_state:
            topic[src_key] = cast(st.session_state[widget_key])


def _apply_preset(preset: dict) -> None:
    """Force-update both topic dict and widget state from a chosen preset."""
    st.session_state["topic"] = dict(preset)
    for src_key, widget_key, cast in TOPIC_FIELDS:
        st.session_state[widget_key] = cast(preset.get(src_key, "" if cast is str else 1))


# ---------------- sections -----------------

def _language_picker() -> None:
    st.subheader(t("sidebar.language"))
    st.radio(
        label=t("sidebar.language"),
        options=AVAILABLE_LANGS,
        format_func=lambda code: LANG_LABELS[code],
        horizontal=True,
        label_visibility="collapsed",
        key="lang",
    )


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

    st.caption(
        t("sidebar.api_active", name=chosen.get("name", "—"))
    )
    if not chosen["api_key"]:
        st.warning(t("sidebar.api_key_missing"))


def _topic_section() -> None:
    st.subheader(t("sidebar.topic_section"))
    frozen = st.session_state.get("topic_frozen", False)

    topics = state.load_topics()
    titles = [tp.get("title", f"#{i}") for i, tp in enumerate(topics)]

    current_title = st.session_state["topic"].get("title", "")
    preset_idx = titles.index(current_title) if current_title in titles else 0

    if "_topic_preset_idx" not in st.session_state:
        st.session_state["_topic_preset_idx"] = preset_idx

    new_idx = st.selectbox(
        t("sidebar.topic_preset_label"),
        options=list(range(len(topics))),
        format_func=lambda i: titles[i],
        disabled=frozen,
        key="_topic_preset_idx",
    )
    if not frozen and titles[new_idx] != current_title:
        _apply_preset(topics[new_idx])
        st.rerun()

    st.text_input(
        t("sidebar.topic_title_label"),
        key="_topic_title",
        disabled=frozen,
    )
    st.text_area(
        t("sidebar.topic_scenario_label"),
        key="_topic_scenario",
        disabled=frozen,
        height=110,
    )
    col1, col2 = st.columns(2)
    with col1:
        st.number_input(
            t("sidebar.topic_shot_count_label"),
            min_value=1,
            max_value=12,
            step=1,
            key="_topic_shots",
            disabled=frozen,
        )
    with col2:
        st.number_input(
            t("sidebar.topic_total_seconds_label"),
            min_value=1,
            max_value=600,
            step=1,
            key="_topic_total",
            disabled=frozen,
        )

    if frozen:
        st.caption(t("sidebar.frozen_hint"))
        return

    if st.button(t("sidebar.topic_save_button"), width="stretch"):
        # Sync widget state -> topic before saving
        _sync_widget_state_to_topic()
        try:
            state.save_topic_preset(dict(st.session_state["topic"]))
            st.success(t("sidebar.topic_saved"))
        except Exception as e:  # noqa: BLE001
            st.error(t("sidebar.topic_save_failed", error=str(e)))


def _researcher_toggle() -> None:
    st.subheader(t("sidebar.researcher_section"))
    st.toggle(t("sidebar.researcher_toggle"), key="researcher_mode")
