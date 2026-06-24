from __future__ import annotations

import streamlit as st

from core import prompts, state
from i18n import get_lang, t
from views import _trial
from views._streaming import stream_llm

# The post-generation loop (paper/7 §2, D24 revision): the script lives in an
# always-editable textarea; the condition's AI channel sits alongside; hand
# edits are snapshotted automatically before submit / any AI action.
#
# Streamlit constraint: a mounted widget key cannot be reassigned in the same
# run, so after an AI action produces a new version we set a refresh flag and
# update "_script_edit" at the top of the next run, before instantiation.


def render(topic: dict, cond: str) -> None:
    _refresh_editor_if_flagged()
    if "_script_edit" not in st.session_state:
        st.session_state["_script_edit"] = state.current_script()

    st.subheader(t("postgen.title", v=len(st.session_state["r_versions"])))
    st.text_area(
        label=t("postgen.title", v=len(st.session_state["r_versions"])),
        key="_script_edit",
        height=320,
        label_visibility="collapsed",
    )
    st.caption(t("postgen.edit_hint"))

    if cond == "D":
        _render_revision_channel(topic)
    elif cond == "E":
        _render_guidance_channel()

    if st.button(t("postgen.submit"), type="primary", width="stretch"):
        final = persist_pending_edit()
        _trial.submit_trial(final)
        st.rerun()


def render_readonly(topic: dict) -> None:
    """Condition C: zero-loop — rendered script + submit only."""
    st.subheader(t("postgen.readonly_title"))
    st.markdown(state.current_script())
    if st.button(t("postgen.submit"), type="primary", width="stretch"):
        _trial.submit_trial(state.current_script())
        st.rerun()


def persist_pending_edit() -> str:
    """Snapshot the editor content as a user_edit version if it differs from
    the current version (the snapshot hard rule). Returns the live text."""
    cur = state.current_script()
    edited = (st.session_state.get("_script_edit") or cur).strip()
    if edited and edited != cur.strip():
        state.add_version(edited, "user_edit")
        return edited
    return cur


def request_editor_refresh() -> None:
    st.session_state["_postgen_refresh"] = True


def _refresh_editor_if_flagged() -> None:
    if st.session_state.pop("_postgen_refresh", False):
        st.session_state["_script_edit"] = state.current_script()


def _render_revision_channel(topic: dict) -> None:
    if st.session_state.pop("_revision_clear", False):
        st.session_state["_revision_input"] = ""
    st.text_area(
        t("d.revision_label"),
        key="_revision_input",
        placeholder=t("d.revision_placeholder"),
        height=80,
    )
    st.caption(t("d.revision_hint"))
    if st.button(t("d.revision_send"), type="secondary", width="stretch"):
        request = (st.session_state.get("_revision_input") or "").strip()
        if not request:
            st.error(t("errors.revision_empty"))
            return
        base = persist_pending_edit()
        st.session_state["r_revision_requests"].append(
            {"round": st.session_state["r_n_ai_rounds"] + 1, "text": request}
        )
        state.log_event("revision_request", {"chars": len(request)})
        lang = get_lang()
        out = stream_llm(
            prompts.build_system_revision(topic, lang),
            prompts.build_user_revision(
                topic, st.session_state["r_intent"], base, request, lang
            ),
            group="D-revise",
        )
        if out is not None:
            state.add_version(out, "ai")
            st.session_state["r_n_ai_rounds"] += 1
            request_editor_refresh()
            st.session_state["_revision_clear"] = True
            st.rerun()


def _render_guidance_channel() -> None:
    if st.button(t("guidance.continue_btn"), type="secondary", width="stretch"):
        persist_pending_edit()
        state.log_event("continue_guidance_click")
        from views import guidance  # local import: guidance imports this module

        guidance.begin_round("ai_from_draft")
        st.rerun()
