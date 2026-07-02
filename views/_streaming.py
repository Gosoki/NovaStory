from __future__ import annotations

import threading
import time
from typing import Optional

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx

from core import llm, state
from i18n import t

# AUD10: the guidance-question call is the one opaque wait (streaming calls show
# tokens as they arrive). Animate a progress bar against the realistic ceiling —
# observed waits are ~30-60s worst case; the bar eases to 95% and completion
# snaps it shut.
_PROGRESS_CEIL_S = 60.0


def _run_with_progress(fn, label: str):
    """Run a blocking LLM call in a worker thread while the script thread
    animates st.progress. The Streamlit ctx is attached so the call may read
    session_state; exceptions propagate to the caller unchanged."""
    result: dict = {}

    def work() -> None:
        try:
            result["value"] = fn()
        except BaseException as e:  # noqa: BLE001 — re-raised below
            result["error"] = e

    th = threading.Thread(target=work, daemon=True)
    add_script_run_ctx(th)
    th.start()
    bar = st.progress(0.0, text=label)
    t0 = time.time()
    while th.is_alive():
        bar.progress(min((time.time() - t0) / _PROGRESS_CEIL_S, 0.95), text=label)
        time.sleep(0.3)
    th.join()
    bar.empty()
    if "error" in result:
        raise result["error"]
    return result.get("value")


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
        elapsed = round(time.time() - t0, 2)
        # Failed waits are still AI-wait, not creative time — count them too,
        # else a retried failure (up to 120s each) inflates t_pregen/t_postgen.
        state.add_llm_wait(elapsed)
        state.log_event("llm_error", {"group": group, "elapsed": elapsed})
        status.update(label=t("llm.failed"), state="error")
        st.error(t("errors.llm_failed", error=str(e)))
        return None

    elapsed = round(time.time() - t0, 2)
    state.add_llm_wait(elapsed)
    state.log_event("llm_done", {"group": group, "elapsed": elapsed,
                                 "usage": st.session_state.get("_last_llm_usage")})
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
        data = _run_with_progress(
            lambda: llm.generate_json(
                system,
                user,
                group=group,
                user_id=str(st.session_state.get("participant_id") or ""),
            ),
            t("guidance.wait"),
        )
    except llm.LLMConfigError:
        st.error(t("errors.no_api_key"))
        return "RETRY"  # transient: caller should offer retry, not degrade
    except llm.LLMCallError as e:
        elapsed = round(time.time() - t0, 2)
        state.add_llm_wait(elapsed)  # failed wait is still AI-wait, not creative time
        state.log_event("llm_error", {"group": group, "elapsed": elapsed, "kind": "call"})
        st.error(t("errors.llm_failed", error=str(e)))
        return "RETRY"
    except llm.LLMJsonError as e:
        # Model responded but JSON was unparseable after retries → degrade to
        # the open fallback question (paper/7 §2).
        elapsed = round(time.time() - t0, 2)
        state.add_llm_wait(elapsed)
        state.log_event("llm_error", {"group": group, "elapsed": elapsed, "kind": "json"})
        return None
    elapsed = round(time.time() - t0, 2)
    state.add_llm_wait(elapsed)
    state.log_event("llm_done", {"group": group, "elapsed": elapsed,
                                 "usage": st.session_state.get("_last_llm_usage")})
    return data
