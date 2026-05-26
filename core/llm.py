from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

import streamlit as st
from openai import OpenAI

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "llm.log"


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


# --------- streaming ---------

def generate_stream(
    system: str,
    user: str,
    *,
    group: str,
    user_id: str = "",
    temperature: float = 0.8,
) -> Iterator[str]:
    """Stream chat completion chunks as they arrive.

    Side effects: appends start / first_token / done / error events to data/llm.log
    so the operator can `tail -f data/llm.log` in another terminal.
    """
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


