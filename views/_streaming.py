from __future__ import annotations

import time
from typing import Optional

import streamlit as st

from core import llm, state
from i18n import t


def stream_llm(system: str, user: str, *, group: str) -> Optional[str]:
    """Render endpoint info, stream tokens via st.write_stream, log lifecycle.

    Returns the full string on success, None on config/call error (already shown
    via st.error). Streaming wait is logged as llm_start/llm_done events and
    accumulated into the round's llm-wait counters (excluded from creative time).
    """
    try:
        llm._client()  # early config check
    except llm.LLMConfigError:
        st.error(t("errors.no_api_key"))
        return None

    status = st.status(t("common.loading"), expanded=True)

    state.log_event("llm_start", {"group": group})
    t0 = time.time()
    try:
        out = st.write_stream(
            llm.stream_clean(
                llm.generate_stream(
                    system,
                    user,
                    group=group,
                    user_id=str(st.session_state.get("participant_id") or ""),
                )
            )
        )
    except llm.LLMCallError as e:
        state.log_event("llm_error", {"group": group, "elapsed": round(time.time() - t0, 2)})
        status.update(label=t("llm.failed"), state="error")
        st.error(t("errors.llm_failed", error=str(e)))
        return None

    elapsed = round(time.time() - t0, 2)
    state.add_llm_wait(elapsed)
    state.log_event("llm_done", {"group": group, "elapsed": elapsed})
    status.update(label=t("llm.completed"), state="complete")
    text = out if isinstance(out, str) else "".join(out)
    return llm.clean_output(text)


def call_llm_json(system: str, user: str, *, group: str) -> Optional[dict]:
    """JSON-mode call (guided elicitation) with the same event/wait bookkeeping.

    Returns the parsed dict, or None on any failure — callers degrade to the
    open fallback question (paper/7 §2)."""
    state.log_event("llm_start", {"group": group})
    t0 = time.time()
    try:
        with st.spinner(t("guidance.wait")):
            data = llm.generate_json(
                system,
                user,
                group=group,
                user_id=str(st.session_state.get("participant_id") or ""),
            )
    except llm.LLMConfigError:
        st.error(t("errors.no_api_key"))
        return "RETRY"  # transient: caller should offer retry, not degrade
    except llm.LLMCallError as e:
        state.log_event(
            "llm_error",
            {"group": group, "elapsed": round(time.time() - t0, 2), "kind": "call"},
        )
        st.error(t("errors.llm_failed", error=str(e)))
        return "RETRY"
    except llm.LLMJsonError as e:
        # Model responded but JSON was unparseable after retries → degrade to
        # the open fallback question (paper/7 §2).
        state.log_event(
            "llm_error",
            {"group": group, "elapsed": round(time.time() - t0, 2), "kind": "json"},
        )
        return None
    elapsed = round(time.time() - t0, 2)
    state.add_llm_wait(elapsed)
    state.log_event("llm_done", {"group": group, "elapsed": elapsed})
    return data
