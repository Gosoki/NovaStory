from __future__ import annotations

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

def generate_stream(
    system: str,
    user: str,
    *,
    group: str,
    user_id: str = "",
    temperature: float | None = None,
) -> Iterator[str]:
    """Stream chat completion chunks as they arrive.

    Side effects: appends start / first_token / done / error events to data/llm.log
    so the operator can `tail -f data/llm.log` in another terminal.
    """
    if temperature is None:
        temperature = config.TEMPERATURE
    model = _model()
    base_url = st.session_state.get("base_url", "")
    client = _client()
    _log(group, user_id, f"start model={model} base={base_url}")
    t0 = time.time()
    total_len = 0
    first = True
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None) or ""
            if not content:
                continue
            if first:
                _log(group, user_id, f"first_token elapsed={time.time()-t0:.2f}s")
                first = False
            total_len += len(content)
            yield content
        _log(group, user_id, f"done elapsed={time.time()-t0:.2f}s len={total_len}")
    except Exception as e:  # noqa: BLE001
        _log(group, user_id, f"error elapsed={time.time()-t0:.2f}s err={e!r}")
        raise LLMCallError(str(e)) from e


# --------- non-streaming (ModeMirror default sampling & dissent) ---------

def generate(
    system: str,
    user: str,
    *,
    group: str,
    user_id: str = "",
    n: int = 1,
    temperature: float | None = None,
) -> list[str]:
    """Return n completions. Tries the API's `n` parameter once, falls back to
    sequential calls for providers that reject it."""
    if temperature is None:
        temperature = config.TEMPERATURE
    model = _model()
    client = _client()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    _log(group, user_id, f"batch_start model={model} n={n}")
    t0 = time.time()
    outs: list[str] = []
    try:
        if n > 1:
            try:
                resp = client.chat.completions.create(
                    model=model, messages=messages, temperature=temperature, n=n
                )
                outs = [(c.message.content or "").strip() for c in resp.choices]
            except Exception:  # noqa: BLE001 — provider may not support n>1
                outs = []
        if len(outs) < n:
            outs = []
            for _ in range(n):
                resp = client.chat.completions.create(
                    model=model, messages=messages, temperature=temperature
                )
                outs.append((resp.choices[0].message.content or "").strip())
        outs = [clean_output(o) for o in outs]
        _log(group, user_id, f"batch_done elapsed={time.time()-t0:.2f}s n={len(outs)}")
        return outs
    except Exception as e:  # noqa: BLE001
        _log(group, user_id, f"batch_error elapsed={time.time()-t0:.2f}s err={e!r}")
        raise LLMCallError(str(e)) from e


