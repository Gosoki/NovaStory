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


def _abandoned(group: str, t0: float) -> None:
    """流式 / JSON 调用被 rerun 打断(误触旧按钮、双击、断线重连)时的收尾记账。

    这段等待仍是 AI 等待、不是创作时间;而且 llm_start 得有一个终止事件,否则
    n_llm_calls 多一条无配对的 llm_start,t_pregen / t_postgen 多算最长 120s。
    下一次 rerun 会自动再生成一次,那是它自己的一对 llm_start / llm_done。"""
    elapsed = round(time.time() - t0, 2)
    try:
        state.add_llm_wait(elapsed)
        state.log_event("llm_abandoned", {"group": group, "elapsed": elapsed})
    except Exception:  # noqa: BLE001 — 收尾记账绝不能再抛
        pass


def stream_llm(system: str, user: str, *, group: str) -> Optional[str]:
    """Render endpoint info, stream tokens via st.write_stream, log lifecycle.

    Returns the full string on success, None on config/call error (already shown
    via st.error). Streaming wait is logged as llm_start/llm_done events and
    accumulated into the round's llm-wait counters (excluded from creative time).
    """
    try:
        llm.ensure_configured()  # early config check
    except llm.LLMConfigError:
        state.log_event("llm_error", {"group": group, "kind": "config"})
        st.error(t("errors.no_api_key"))
        return None

    status = st.status(t("common.loading"), expanded=True)

    state.log_event("llm_start", {"group": group})
    t0 = time.time()
    settled = False   # 三条正常出口(成功 / 调用失败 / 空流)都置真;没置真就离开 = 被 rerun 打断
    try:
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
            settled = True
            # Failed waits are still AI-wait, not creative time — count them too,
            # else a retried failure (up to 120s each) inflates t_pregen/t_postgen.
            state.add_llm_wait(elapsed)
            state.log_event("llm_error", {"group": group, "elapsed": elapsed})
            status.update(label=t("llm.failed"), state="error")
            # 上游异常原文(org id / 网关中英文文案 / 模型名)不给被试看:细节已在 data/llm.log
            st.error(t("errors.llm_failed"))
            return None

        elapsed = round(time.time() - t0, 2)
        settled = True
        state.add_llm_wait(elapsed)
        text = llm.clean_output(out if isinstance(out, str) else "".join(out))
        # A congested gateway can close the stream having sent nothing at all — no
        # exception, just empty content (`generate_json` already treats that as a
        # failed attempt; the streaming path used to drop through). Without this the
        # callers' `if out and out.strip()` guard fails SILENTLY: the participant
        # clicks 「AIに伝える」/「回答を終えて生成」and nothing happens, while the
        # status bar still reads "generation complete". Same handling as a raised
        # error — log it, say it, return None — so every caller's existing failure
        # branch (retry button / keep the draft) engages.
        if not text:
            state.log_event("llm_error", {"group": group, "elapsed": elapsed, "kind": "empty"})
            status.update(label=t("llm.failed"), state="error")
            st.error(t("errors.llm_empty"))
            return None
        state.log_event("llm_done", {"group": group, "elapsed": elapsed,
                                     "usage": st.session_state.get("_last_llm_usage"),
                                     "repro": st.session_state.get("_last_llm_repro")})
        status.update(label=t("llm.completed"), state="complete")
        return text
    finally:
        if not settled:
            _abandoned(group, t0)

# 哨兵:临时失败(配置缺失 / 网关报错),调用方应提供「重试」而不是降级成开放题。
# 以前用字符串 "RETRY" 当哨兵,与 Optional[dict] 的注解打架,而且模型真返回一个
# 字符串时无法区分。
RETRY = object()


def call_llm_json(system: str, user: str, *, group: str) -> dict | None | object:
    """JSON-mode call (guided elicitation) with the same event/wait bookkeeping.

    Returns the parsed dict; `RETRY` on a transient failure (caller offers a retry
    button); None when the model answered but never produced valid JSON — callers
    degrade to the open fallback question (paper/7 §2)."""
    state.log_event("llm_start", {"group": group})
    t0 = time.time()
    settled = False
    try:
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
            settled = True
            # 与另外两个 except 分支同一套记账:缺配置也是一次失败的调用,要留痕
            state.log_event("llm_error", {"group": group, "kind": "config"})
            st.error(t("errors.no_api_key"))
            return RETRY  # transient: caller should offer retry, not degrade
        except llm.LLMCallError as e:
            elapsed = round(time.time() - t0, 2)
            settled = True
            state.add_llm_wait(elapsed)  # failed wait is still AI-wait, not creative time
            state.log_event("llm_error", {"group": group, "elapsed": elapsed, "kind": "call"})
            st.error(t("errors.llm_failed"))
            return RETRY
        except llm.LLMJsonError as e:
            # Model responded but JSON was unparseable after retries → degrade to
            # the open fallback question (paper/7 §2).
            elapsed = round(time.time() - t0, 2)
            settled = True
            state.add_llm_wait(elapsed)
            state.log_event("llm_error", {"group": group, "elapsed": elapsed, "kind": "json"})
            return None
        elapsed = round(time.time() - t0, 2)
        settled = True
        state.add_llm_wait(elapsed)
        state.log_event("llm_done", {"group": group, "elapsed": elapsed,
                                     "usage": st.session_state.get("_last_llm_usage"),
                                     "repro": st.session_state.get("_last_llm_repro")})
        return data
    finally:
        if not settled:
            _abandoned(group, t0)
