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

    _render_history()

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


def _render_history() -> None:
    """Collapsible log of past requests / guidance answers and earlier drafts so
    the participant can look back at what they asked for and previous versions."""
    versions = st.session_state["r_versions"]
    requests = st.session_state["r_revision_requests"]
    guidance = st.session_state["r_guidance_rounds"]
    if len(versions) <= 1 and not requests and not guidance:
        return
    with st.expander(t("history.title"), expanded=False):
        if requests:
            st.caption(t("history.requests"))
            for r in requests:
                st.markdown(f"- {t('history.round', n=r['round'])}: {r['text']}")
        if guidance:
            st.caption(t("history.guidance"))
            for g in guidance:
                chosen = "、".join(
                    it["chosen"] for it in g["items"] if it.get("chosen")
                )
                st.markdown(f"- {t('history.round', n=g['round'])}: {chosen or '—'}")
        if len(versions) > 1:
            st.caption(t("history.versions"))
            past = versions[:-1]
            labels = [
                t("history.version", v=v["v"], who=t(f"history.author_{v['author']}"))
                for v in past
            ]
            i = st.selectbox(
                t("history.pick"), range(len(past)),
                format_func=lambda x: labels[x],
                index=len(past) - 1,
                key=f"_hist_pick_{st.session_state['round_idx']}",
            )
            st.markdown(past[i]["text"])


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
    if st.button(t("d.revision_send"), type="secondary", width="stretch", key="btn_more_ai"):
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
    if st.button(t("guidance.continue_btn"), type="secondary", width="stretch", key="btn_more_ai"):
        persist_pending_edit()
        state.log_event("continue_guidance_click")
        from views import guidance  # local import: guidance imports this module

        guidance.begin_round("ai_from_draft")
        st.rerun()
