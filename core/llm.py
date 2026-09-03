from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

import streamlit as st
from openai import OpenAI

from core import config

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "llm.log"

# Per-request timeout (seconds). The OpenAI SDK defaults to ~600s, so a congested
# free gateway (edgefn returns 503 "渠道繁忙"/ChannelNotEnough) can hang a single
# request for ~10 min before erroring — a participant would stare at a spinner the
# whole time. Cap it so a stuck call fails fast and the pre-first-token auto-retry
# (or the user's manual retry) kicks in instead. Legit slow calls finish well
# under this (observed: final gen <40s, guidance JSON up to ~92s).
REQUEST_TIMEOUT = 120


# Reasoning models (e.g. DeepSeek-R1) may leak chain-of-thought into content.
_THINK_RE = re.compile(r"<(think|thinking|reasoning)>.*?(?:</\1>|\Z)\s*", re.DOTALL | re.IGNORECASE)
_THINK_OPEN, _THINK_CLOSE = "<think>", "</think>"


def clean_output(text: str) -> str:
    """Strip leaked <think> blocks; applied to every completion before storage."""
    return _THINK_RE.sub("", text or "").strip()


def _partial_tag_len(s: str, tag: str) -> int:
    """Length of the longest suffix of s that is a (proper) prefix of tag, so a
    tag split across streaming chunk boundaries isn't emitted prematurely."""
    for k in range(min(len(s), len(tag) - 1), 0, -1):
        if tag.startswith(s[-k:]):
            return k
    return 0


def stream_clean(chunks: Iterator[str]) -> Iterator[str]:
    """Strip <think>…</think> reasoning from a LIVE token stream so the participant
    never sees raw chain-of-thought scroll by (clean_output only cleans the final
    stored string — too late for the on-screen render). Incremental: holds back
    only a tiny tail that could be a split tag; drops reasoning content as it goes."""
    buf = ""
    in_think = False
    for chunk in chunks:
        buf += chunk
        out = ""
        while buf:
            if not in_think:
                i = buf.lower().find(_THINK_OPEN)
                if i == -1:
                    keep = _partial_tag_len(buf, _THINK_OPEN)
                    out += buf[: len(buf) - keep]
                    buf = buf[len(buf) - keep :]
                    break
                out += buf[:i]
                buf = buf[i + len(_THINK_OPEN) :]
                in_think = True
            else:
                j = buf.lower().find(_THINK_CLOSE)
                if j == -1:
                    keep = _partial_tag_len(buf, _THINK_CLOSE)
                    buf = buf[len(buf) - keep :]  # drop reasoning, keep partial-tag tail
                    break
                buf = buf[j + len(_THINK_CLOSE) :]
                in_think = False
        if out:
            yield out
    if buf and not in_think:
        yield buf


class LLMConfigError(RuntimeError):
    """Raised when API key / base url / model are missing or invalid."""


class LLMCallError(RuntimeError):
    """Raised when the upstream call itself fails."""


class LLMJsonError(RuntimeError):
    """Raised when a JSON-mode call cannot produce parseable JSON after retries."""


# --------- logging ---------

def _log(group: str, user_id: str, msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"{ts} [{group}] user={user_id or '-'} {msg}\n"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass  # logging must never break the experiment


# --------- client ---------

def _client() -> OpenAI:
    api_key = (st.session_state.get("api_key") or "").strip()
    base_url = (st.session_state.get("base_url") or "").strip()
    if not api_key:
        raise LLMConfigError("missing_api_key")
    # max_retries=0:重试由 generate_stream / generate_json 自己的循环独占(留 llm.log 痕迹)。
    # SDK 默认再叠 2 次静默重试 → 最坏等待 3×3×120s,被试对着转圈十几分钟。
    kwargs = {"api_key": api_key, "timeout": REQUEST_TIMEOUT, "max_retries": 0}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def ensure_configured() -> None:
    """只做配置检查(api_key 在不在),不发请求;缺配置抛 LLMConfigError。"""
    _client()


def _model() -> str:
    return (st.session_state.get("model") or "").strip() or "gpt-4o-mini"


def _seed() -> int:
    """Per-trial deterministic seed, sent on every call (B7, 2026-08-03).

    OpenAI's `seed` is best-effort only — measured on gpt-4o-mini, 5/5 runs
    differ even at temperature=0 with a fixed seed (batched inference is not
    bit-reproducible). We still pin it so the request is fully specified and a
    re-run is as close as the API allows; the logged seed + system_fingerprint
    are what make drift detectable after the fact.

    Derived from (participant, round) rather than a single global constant so
    participants stay independent — one shared seed would nudge everyone toward
    the same sample and shrink the between-participant variance H6 measures.
    """
    pid = st.session_state.get("participant_id") or 0
    rnd = st.session_state.get("round_idx") or 0
    return (int(pid) * 100 + int(rnd)) % (2**31)


def current_meta(temperature: float | None = None) -> dict:
    """Generation parameters as actually used — logged into every trial row."""
    return {
        "model": _model(),
        "temperature": config.TEMPERATURE if temperature is None else temperature,
        "base_url": (st.session_state.get("base_url") or "").strip(),
    }


# --------- token usage (LOG6) ---------
# Stashed per call in session_state (generate_stream is a generator consumed by
# st.write_stream, so it can't return usage); the _streaming wrappers read it
# into the llm_done event payload. None when the gateway doesn't report usage.

def _stash_usage(usage) -> None:
    st.session_state["_last_llm_usage"] = None if usage is None else {
        "prompt": getattr(usage, "prompt_tokens", None),
        "completion": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _begin_call_trace(seed: int) -> None:
    """新一次调用开始:清掉上一次的 usage / fingerprint,记下本次 seed。"""
    _stash_usage(None)
    _stash_repro(None)
    _stash_repro(seed)


def _stash_repro(seed: int | None, fingerprint=None) -> None:
    """Reproducibility trace for the llm_done event (B7): the seed we sent and
    the backend build OpenAI served it from. A fingerprint change mid-collection
    means the serving stack moved under us — that is what we need to be able to
    report, since the outputs themselves are not bit-reproducible."""
    if seed is None and fingerprint is None:
        st.session_state["_last_llm_repro"] = None
        return
    cur = st.session_state.get("_last_llm_repro") or {}
    if seed is not None:
        cur["seed"] = seed
    if fingerprint is not None:
        cur["system_fingerprint"] = fingerprint
    st.session_state["_last_llm_repro"] = cur


# --------- streaming ---------

def ping(max_tokens: int = 5) -> tuple[bool, float, str]:
    """Researcher connectivity check: a tiny non-streaming completion against the
    currently configured endpoint. Returns (ok, elapsed_seconds, detail).

    Never raises — surfaces config/network/server-busy problems as detail text so
    the researcher can confirm the model is reachable before a participant starts.
    """
    t0 = time.time()
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=_model(),
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=max_tokens,
            temperature=0,
        )
        txt = (resp.choices[0].message.content or "").strip()
        return True, time.time() - t0, txt or "(空の応答)"
    except LLMConfigError as e:
        return False, time.time() - t0, f"config: {e}"
    except Exception as e:  # noqa: BLE001
        return False, time.time() - t0, str(e)


def generate_stream(
    system: str,
    user: str,
    *,
    group: str,
    user_id: str = "",
    temperature: float | None = None,
    retries: int = 2,
) -> Iterator[str]:
    """Stream chat completion chunks as they arrive.

    Transient failures (server busy / rate limit / timeout) are auto-retried with
    a short backoff — but ONLY while no token has been streamed yet, since once
    content is yielded to the UI a retry would duplicate output. After streaming
    begins, an error propagates (the caller's manual retry button handles it).

    Side effects: appends start / first_token / retry / done / error events to
    data/llm.log so the operator can `tail -f data/llm.log` in another terminal.
    """
    if temperature is None:
        temperature = config.TEMPERATURE
    model = _model()
    base_url = st.session_state.get("base_url", "")
    seed = _seed()
    client = _client()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    _log(group, user_id, f"start model={model} base={base_url} seed={seed}")
    t0 = time.time()
    _begin_call_trace(seed)
    for attempt in range(retries + 1):
        total_len = 0
        first = True
        try:
            stream = client.chat.completions.create(
                model=model, messages=messages, temperature=temperature, stream=True,
                seed=seed, stream_options={"include_usage": True},
            )
            for chunk in stream:
                if getattr(chunk, "system_fingerprint", None):
                    _stash_repro(None, chunk.system_fingerprint)
                # 服务端回显的 model:正式快照(gpt-5.4-mini)不返回 system_fingerprint,这一项是
                # 「服务的确是所请求的快照」的唯一证据
                if getattr(chunk, "model", None):
                    st.session_state.setdefault("_last_llm_repro", {}).setdefault("served_model", chunk.model)
                # The usage chunk arrives last with choices=[] — read it before
                # the empty-choices skip below.
                if getattr(chunk, "usage", None):
                    _stash_usage(chunk.usage)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None) or ""
                if not content:
                    continue
                if first:
                    _log(group, user_id,
                         f"first_token elapsed={time.time()-t0:.2f}s attempt={attempt+1}")
                    first = False
                total_len += len(content)
                yield content
            _log(group, user_id,
                 f"done elapsed={time.time()-t0:.2f}s len={total_len}"
                 f" usage={st.session_state.get('_last_llm_usage')}")
            return
        except Exception as e:  # noqa: BLE001
            if first and attempt < retries:  # safe to retry: nothing streamed yet
                _log(group, user_id, f"retry attempt={attempt+1} err={e!r}")
                time.sleep(1.5 * (attempt + 1))
                continue
            _log(group, user_id, f"error elapsed={time.time()-t0:.2f}s err={e!r}")
            raise LLMCallError(str(e)) from e


# --------- JSON mode (guided elicitation) ---------

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _guidance_client_and_model() -> tuple[OpenAI, str]:
    """Guidance step may use its own API config (paper/7 D23)."""
    idx = config.GUIDANCE_API_INDEX
    if idx is None:
        return _client(), _model()
    try:
        cfg = st.secrets.get("api_configs", [])[idx]
    except Exception as e:  # noqa: BLE001
        raise LLMConfigError(f"guidance api_configs[{idx}] unavailable") from e
    if not (cfg.get("api_key") or "").strip():
        raise LLMConfigError(f"guidance api_configs[{idx}] missing api_key")
    client = OpenAI(
        api_key=cfg["api_key"], base_url=cfg.get("base_url") or None,
        timeout=REQUEST_TIMEOUT, max_retries=0,
    )
    return client, (cfg.get("model") or "").strip() or "gpt-4o-mini"


def generate_json(
    system: str,
    user: str,
    *,
    group: str,
    user_id: str = "",
    retries: int | None = None,
    temperature: float | None = None,
) -> dict:
    """Call the guidance model and parse its output as JSON.

    Retries with the parse error fed back; raises LLMJsonError when every
    attempt fails (callers degrade to an open fallback question)."""
    if retries is None:
        retries = config.GUIDANCE_JSON_RETRIES
    if temperature is None:
        temperature = config.TEMPERATURE
    client, model = _guidance_client_and_model()
    seed = _seed()
    _log(group, user_id, f"json_start model={model} seed={seed}")
    t0 = time.time()
    _begin_call_trace(seed)
    spent: dict = {}
    last_err = ""
    msg_user = user
    for attempt in range(retries + 1):
        # OpenAI 原生接口支持 JSON 模式(system 里已有「JSON」字样,满足其前置要求);第三方网关
        # 不一定认这个参数,只对 openai.com 开。三次都不是合法 JSON 才降级成开放题 = 换掉了条件,
        # 能少一次是一次。
        kwargs = {}
        if "openai.com" in (st.session_state.get("base_url") or ""):
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": msg_user},
                ],
                temperature=temperature,
                seed=seed,
                **kwargs,
            )
        except Exception as e:  # noqa: BLE001
            _log(group, user_id, f"json_error elapsed={time.time()-t0:.2f}s err={e!r}")
            raise LLMCallError(str(e)) from e
        fp = getattr(resp, "system_fingerprint", None)
        if fp:   # None 时别调:两参皆 None 是「清空」的约定,会把刚存的 seed 抹掉
            _stash_repro(None, fp)
        if getattr(resp, "model", None):
            st.session_state.setdefault("_last_llm_repro", {})["served_model"] = resp.model
        usage = getattr(resp, "usage", None)
        if usage:  # accumulate across JSON-retry attempts — cost is what we track
            for k, attr in (("prompt", "prompt_tokens"), ("completion", "completion_tokens"),
                            ("total_tokens", "total_tokens")):
                v = getattr(usage, attr, None)
                if v is not None:
                    spent[k] = spent.get(k, 0) + v
            st.session_state["_last_llm_usage"] = dict(spent)
        # A congested free gateway can return empty choices / null content. Treat
        # it as a failed attempt (retry → LLMJsonError → caller degrades to the
        # open fallback), not an IndexError that escapes the LLM-error taxonomy and
        # crashes the guidance step.
        choices = getattr(resp, "choices", None) or []
        msg = choices[0].message if choices else None
        raw = _FENCE_RE.sub("", clean_output(getattr(msg, "content", None) or "")).strip()
        # 模型偶尔在 JSON 前后加一句话:抠出最外层 {...} 再解析,别把整轮引导浪费在一句「以下是问题:」上
        m_obj = re.search(r"\{.*\}", raw, re.DOTALL)
        if m_obj and not raw.startswith("{"):
            raw = m_obj.group(0)
        try:
            if not raw:
                raise json.JSONDecodeError("empty response from model", "", 0)
            data = json.loads(raw)
            _log(group, user_id,
                 f"json_done elapsed={time.time()-t0:.2f}s attempt={attempt + 1}")
            return data
        except json.JSONDecodeError as e:
            last_err = str(e)
            _log(group, user_id, f"json_parse_fail attempt={attempt + 1} err={last_err}")
            # Neutral English so the correction note never nudges the output
            # language away from the participant's locale.
            msg_user = (
                f"{user}\n\n(Your previous output was not valid JSON. "
                f"Parse error: {last_err}. Output ONLY valid JSON, nothing else.)"
            )
    raise LLMJsonError(last_err)


