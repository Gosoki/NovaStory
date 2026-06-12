from __future__ import annotations

import streamlit as st

from core import config, prompts, state
from i18n import t
from views import group_d
from views._streaming import call_llm_batch, stream_llm
from views._trial import render_final_and_confirm

_ADJUDICATIONS = ("accept", "transform", "reject")


def render_pipeline(topic: dict) -> None:
    """Condition E — outline checkpoint + ModeMirror dissent.

    intent → AI outline (+ pre-sampled defaults) → human edit
    → dissent (overlap / question / counter-proposal) → forced adjudication
    → optional re-edit → final.
    """
    intent = st.session_state["r_intent"]

    # Step 1: outline + default samples (one wait window)
    if not st.session_state["r_outline_ai"]:
        system = prompts.build_system_outline(topic)
        user = prompts.build_user(topic, intent)
        out = stream_llm(system, user, group="E-outline")
        if out is None:
            return
        st.session_state["r_outline_ai"] = out
        st.session_state["r_outline_user"] = out
        state.log_event("outline_shown")
        defaults = call_llm_batch(
            system, user, group="E-defaults", n=config.N_DEFAULT_SAMPLES
        )
        if defaults:
            st.session_state["r_defaults"] = defaults
        st.rerun()
        return

    # Step 2: edit, then request dissent
    if not st.session_state["r_dissent"]:
        outline = group_d.render_outline_editor()
        if st.button(t("mm.request"), type="secondary", width="stretch"):
            if not outline:
                st.error(t("errors.outline_empty"))
                return
            st.session_state["r_outline_user"] = outline
            state.log_event("dissent_request", {"edited_chars": len(outline)})
            _gen_dissent(topic, intent, outline)
        return

    # Step 3: forced adjudication
    if not st.session_state["r_adjudication"]:
        _show_dissent(expanded=True)
        choice = st.radio(
            t("mm.adjudicate_label"),
            _ADJUDICATIONS,
            format_func=lambda c: t(f"mm.adj_{c}"),
            index=None,
            key="_mm_choice",
        )
        reason = st.text_input(t("mm.reason_label"), key="_mm_reason")
        if st.button(
            t("mm.adjudicate_submit"),
            type="primary",
            disabled=choice is None,
            width="stretch",
        ):
            st.session_state["r_adjudication"] = choice
            st.session_state["r_adjudication_reason"] = (reason or "").strip()
            state.log_event("adjudicate", {"choice": choice})
            st.rerun()
        return

    # Step 4: post-adjudication edit → final
    if not st.session_state["r_final"]:
        _show_dissent(expanded=False)
        adj = st.session_state["r_adjudication"]
        st.caption(
            t("mm.post_adj_accept" if adj in ("accept", "transform") else "mm.post_adj_reject")
        )
        outline = group_d.render_outline_editor()
        if st.button(t("group_d.gen_final"), type="secondary", width="stretch"):
            if not outline:
                st.error(t("errors.outline_empty"))
                return
            st.session_state["r_outline_user"] = outline
            state.log_event("final_click", {"edited_chars": len(outline)})
            group_d.gen_final(topic, intent, outline, group="E-final")
        return

    render_final_and_confirm(st.session_state["r_final"])


def _gen_dissent(topic: dict, intent: str, outline: str) -> None:
    # Defaults may have failed at step 1 — retry here before dissent.
    if not st.session_state["r_defaults"]:
        defaults = call_llm_batch(
            prompts.build_system_outline(topic),
            prompts.build_user(topic, intent),
            group="E-defaults",
            n=config.N_DEFAULT_SAMPLES,
        )
        if not defaults:
            return
        st.session_state["r_defaults"] = defaults
    system = prompts.build_system_dissent(topic, st.session_state["divergence_dim"])
    user = prompts.build_user_dissent(
        topic, intent, outline, st.session_state["r_defaults"]
    )
    outs = call_llm_batch(system, user, group="E-dissent", n=1)
    if outs:
        st.session_state["r_dissent"] = outs[0]
        state.log_event("dissent_shown")
        st.rerun()


def _show_dissent(expanded: bool) -> None:
    with st.expander(t("mm.dissent_title"), expanded=expanded):
        st.markdown(st.session_state["r_dissent"])
