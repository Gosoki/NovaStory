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


# Reasoning models (e.g. DeepSeek-R1) may leak chain-of-thought into content.
_THINK_RE = re.compile(r"<think>.*?(?:</think>|\Z)\s*", re.DOTALL)


def clean_output(text: str) -> str:
    """Strip leaked <think> blocks; applied to every completion before storage."""
    return _THINK_RE.sub("", text or "").strip()


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
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _model() -> str:
    return (st.session_state.get("model") or "").strip() or "gpt-4o-mini"


def current_meta(temperature: float | None = None) -> dict:
    """Generation parameters as actually used — logged into every trial row."""
    return {
        "model": _model(),
        "temperature": config.TEMPERATURE if temperature is None else temperature,
        "base_url": (st.session_state.get("base_url") or "").strip(),
    }


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
    client = _client()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    _log(group, user_id, f"start model={model} base={base_url}")
    t0 = time.time()
    for attempt in range(retries + 1):
        total_len = 0
        first = True
        try:
            stream = client.chat.completions.create(
                model=model, messages=messages, temperature=temperature, stream=True,
            )
            for chunk in stream:
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
            _log(group, user_id, f"done elapsed={time.time()-t0:.2f}s len={total_len}")
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
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg.get("base_url") or None)
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
    _log(group, user_id, f"json_start model={model}")
    t0 = time.time()
    last_err = ""
    msg_user = user
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": msg_user},
                ],
                temperature=temperature,
            )
        except Exception as e:  # noqa: BLE001
            _log(group, user_id, f"json_error elapsed={time.time()-t0:.2f}s err={e!r}")
            raise LLMCallError(str(e)) from e
        raw = clean_output(resp.choices[0].message.content or "")
        raw = _FENCE_RE.sub("", raw).strip()
        try:
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


