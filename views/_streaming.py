from __future__ import annotations

import hashlib
import threading
import time
from typing import Optional

import streamlit as st

from core import llm, state
from i18n import t

# AUD10: the guidance-question call is the one opaque wait (streaming calls show
# tokens as they arrive). Animate a progress bar against a realistic ceiling —
# 中国队列实测 E 出题中位 6.6 s、p90 8 s(2026-10-29 审计)。原来按 60 s 走,6 秒时只到 10% 就收掉,
# 看着像卡住,被试于是去点屏幕上残留的旧按钮(见下面「在途调用」)。eases to 95%, completion snaps it shut.
_PROGRESS_CEIL_S = 12.0

# ---- 在途调用(2026-10-29 审计)----
# Streamlit 默认 fastReruns:等待中一点旧按钮、双击、手机休眠后重连、按返回键,都会停掉当前脚本
# 立刻重跑。以前调用跑在脚本线程里(或跑在会去碰 session_state 的线程里),一停就作废,重跑走到同一步
# 再发一次 —— 中国队列 llm_start 356 次只有 159 次结束,E 出题 108/144 次被打断、被试最多连点 43 次;
# C 有 3 人先看到半份稿子再被换成另一份(破坏「只生成一次」)。原来的 llm_abandoned 记账也从未生效
# (停掉之后连 finally 里读 session_state 都会再抛)。
# 现在:调用在后台线程里跑,线程只用调用前取好的配置副本(core.llm._tls),不碰 session_state,
# 脚本被停也照样跑完;按 (组, 被试, 轮, attempt, prompt 哈希) 登记在 session_state。下一次运行走到
# 同一步、发同一个请求时 → 接着等原来那个 / 直接取它的结果,不重发;每次「接着等」记一条
# llm_reattach(日本队列「请求重启」敏感性的读数,prereg.JA_SENSITIVITY)。等待时长从第一次发出算到
# 拿到结果,只记一次(llm_done 的 elapsed 与 add_llm_wait 都含被打断的那段)。
_INFLIGHT = "_llm_inflight"
_FORGET_AFTER_S = 600   # 已结束、没人来取的调用(会话放弃 / 换了 attempt)留这么久后清掉
# 等待中的标记:app.py 的 CSS 见到「本次运行里有它」,就把上一次运行残留、还没被重画的旧元素
# 藏起来、不可点(只在有 AI 调用的那次运行里;问卷页等其他运行不受影响)。
_BUSY = '<span class="ns-busy"></span>'


def _job_key(group: str, system: str, user: str) -> str:
    ss = st.session_state
    h = hashlib.sha1(f"{system}\x1f{user}".encode("utf-8")).hexdigest()[:16]
    return "|".join(str(x) for x in (group, ss.get("participant_id"), ss.get("round_idx"),
                                     ss.get("r_attempt"), h))


def _job(key: str, run) -> tuple[dict, bool]:
    """取同一请求的在途调用,没有就在后台线程里发起一次。返回 (job, 是否新发起)。
    `run(job)` 在线程里执行:可以往 job["chunks"] 追加流式片段,返回值存 job["value"]。"""
    jobs = st.session_state.setdefault(_INFLIGHT, {})
    now = time.time()
    for k in [k for k, j in jobs.items() if j["done"] and now - j["t0"] > _FORGET_AFTER_S]:
        jobs.pop(k, None)
    if key in jobs:
        return jobs[key], False
    ss = st.session_state
    job = {"t0": now, "done": False, "chunks": [], "value": None, "error": None, "settled": None,
           "state": {k: ss.get(k) for k in ("api_key", "base_url", "model", "participant_id", "round_idx")}}

    def work() -> None:
        llm._tls.state = job["state"]
        try:
            job["value"] = run(job)
        except BaseException as e:  # noqa: BLE001 — 交给主线程按原来的分支处理
            job["error"] = e
        finally:
            llm._tls.state = None
            job["done"] = True

    jobs[key] = job
    threading.Thread(target=work, daemon=True).start()
    return job, True


def _log_start(group: str, job: dict, new: bool) -> None:
    if new:
        state.log_event("llm_start", {"group": group})
    else:
        state.log_event("llm_reattach", {"group": group, "since": round(time.time() - job["t0"], 2),
                                         "done": bool(job["done"])})


def _forget(key: str) -> None:
    st.session_state.get(_INFLIGHT, {}).pop(key, None)


def stream_llm(system: str, user: str, *, group: str) -> Optional[str]:
    """Render endpoint info, stream tokens, log lifecycle.

    Returns the full string on success, None on config/call error (already shown
    via st.error). Streaming wait is logged as llm_start/llm_done events and
    accumulated into the round's llm-wait counters (excluded from creative time).
    被 rerun 打断后,下一次运行用同一请求接着显示原来那份,不重新生成(见上面「在途调用」)。
    """
    try:
        llm.ensure_configured()  # early config check
    except llm.LLMConfigError:
        state.log_event("llm_error", {"group": group, "kind": "config"})
        st.error(t("errors.no_api_key"))
        return None

    key = _job_key(group, system, user)
    uid = str(st.session_state.get("participant_id") or "")

    def run(job: dict) -> None:
        for chunk in llm.stream_clean(llm.generate_stream(system, user, group=group, user_id=uid)):
            job["chunks"].append(chunk)

    job, new = _job(key, run)
    _log_start(group, job, new)
    st.markdown(_BUSY, unsafe_allow_html=True)
    status = st.status(t("common.loading"), expanded=True)
    out = st.empty()
    while not job["done"]:
        out.markdown("".join(job["chunks"]))
        time.sleep(0.15)
    raw = "".join(job["chunks"])
    out.markdown(raw)

    if job["settled"] is None:   # 记账只做一次:被打断在记账之后、取走之前,下一次运行不重复记
        elapsed = round(time.time() - job["t0"], 2)
        err = job["error"]
        if err is not None and not isinstance(err, llm.LLMCallError):
            _forget(key)
            raise err
        # Failed waits are still AI-wait, not creative time — count them too,
        # else a retried failure (up to 120s each) inflates t_pregen/t_postgen.
        state.add_llm_wait(elapsed)
        text = llm.clean_output(raw)
        if err is not None:
            state.log_event("llm_error", {"group": group, "elapsed": elapsed})
            job["settled"] = ("failed", None)
        # A congested gateway can close the stream having sent nothing at all — no
        # exception, just empty content (`generate_json` already treats that as a
        # failed attempt). Without this the callers' `if out and out.strip()` guard
        # fails SILENTLY: the participant clicks 「AIに伝える」/「回答を終えて生成」and
        # nothing happens, while the status bar still reads "generation complete".
        elif not text:
            state.log_event("llm_error", {"group": group, "elapsed": elapsed, "kind": "empty"})
            job["settled"] = ("empty", None)
        else:
            state.log_event("llm_done", {"group": group, "elapsed": elapsed,
                                         "usage": job["state"].get("_last_llm_usage"),
                                         "repro": job["state"].get("_last_llm_repro")})
            job["settled"] = ("ok", text)
    _forget(key)
    kind, text = job["settled"]
    if kind == "ok":
        status.update(label=t("llm.completed"), state="complete")
        return text
    status.update(label=t("llm.failed"), state="error")
    # 上游异常原文(org id / 网关中英文文案 / 模型名)不给被试看:细节已在 data/llm.log
    st.error(t("errors.llm_failed") if kind == "failed" else t("errors.llm_empty"))
    return None


# 哨兵:临时失败(配置缺失 / 网关报错),调用方应提供「重试」而不是降级成开放题。
# 以前用字符串 "RETRY" 当哨兵,与 Optional[dict] 的注解打架,而且模型真返回一个
# 字符串时无法区分。
RETRY = object()


def call_llm_json(system: str, user: str, *, group: str) -> dict | None | object:
    """JSON-mode call (guided elicitation) with the same event/wait bookkeeping.

    Returns the parsed dict; `RETRY` on a transient failure (caller offers a retry
    button); None when the model answered but never produced valid JSON — callers
    degrade to the open fallback question (paper/7 §2).
    被 rerun 打断后接着等原来那次调用,进度条也从原来的进度接着走(见上面「在途调用」)。"""
    key = _job_key(group, system, user)
    uid = str(st.session_state.get("participant_id") or "")
    job, new = _job(key, lambda job: llm.generate_json(system, user, group=group, user_id=uid))
    _log_start(group, job, new)
    st.markdown(_BUSY, unsafe_allow_html=True)
    label = t("guidance.wait")
    bar = st.progress(0.0, text=label)
    while not job["done"]:
        bar.progress(min((time.time() - job["t0"]) / _PROGRESS_CEIL_S, 0.95), text=label)
        time.sleep(0.3)
    bar.empty()

    if job["settled"] is None:   # 记账只做一次(同 stream_llm)
        elapsed = round(time.time() - job["t0"], 2)
        err = job["error"]
        if isinstance(err, llm.LLMConfigError):
            # 与另外两个失败分支同一套记账:缺配置也是一次失败的调用,要留痕
            state.log_event("llm_error", {"group": group, "kind": "config"})
            job["settled"] = ("config", None)
        elif isinstance(err, llm.LLMCallError):
            state.add_llm_wait(elapsed)  # failed wait is still AI-wait, not creative time
            state.log_event("llm_error", {"group": group, "elapsed": elapsed, "kind": "call"})
            job["settled"] = ("call", None)
        elif isinstance(err, llm.LLMJsonError):
            # Model responded but JSON was unparseable after retries → degrade to
            # the open fallback question (paper/7 §2).
            state.add_llm_wait(elapsed)
            state.log_event("llm_error", {"group": group, "elapsed": elapsed, "kind": "json"})
            job["settled"] = ("json", None)
        elif err is not None:
            _forget(key)
            raise err
        else:
            state.add_llm_wait(elapsed)
            state.log_event("llm_done", {"group": group, "elapsed": elapsed,
                                         "usage": job["state"].get("_last_llm_usage"),
                                         "repro": job["state"].get("_last_llm_repro")})
            job["settled"] = ("ok", job["value"])
    _forget(key)
    kind, data = job["settled"]
    if kind == "config":
        st.error(t("errors.no_api_key"))
        return RETRY  # transient: caller should offer retry, not degrade
    if kind == "call":
        st.error(t("errors.llm_failed"))
        return RETRY
    if kind == "json":
        return None
    return data
