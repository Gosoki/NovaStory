from __future__ import annotations

import time
import tomllib
from pathlib import Path

from openai import OpenAI

from core.llm import clean_output

"""T7.1 — offline batch LLM client.

Unlike core.llm this never touches st.session_state: api_key / base_url /
model / temperature are passed explicitly (or read from secrets.toml), so
the scripts/ and analysis/ pipelines can run outside Streamlit.
"""

ROOT = Path(__file__).resolve().parent.parent
SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"


class BatchClient:
    """Plain (non-Streamlit) chat-completion client with retry."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.8,
    ) -> None:
        if not (api_key or "").strip():
            raise ValueError("missing api_key")
        if not (model or "").strip():
            raise ValueError("missing model")
        self.model = model.strip()
        self.temperature = temperature
        self.base_url = (base_url or "").strip()
        kwargs: dict = {"api_key": api_key.strip()}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self._client = OpenAI(**kwargs)

    @classmethod
    def from_secrets(cls, index: int = 0, temperature: float = 0.8) -> "BatchClient":
        """Build from `[[api_configs]]` #index in .streamlit/secrets.toml
        (the file is gitignored but present locally)."""
        if not SECRETS_PATH.exists():
            raise FileNotFoundError(
                f"{SECRETS_PATH} 不存在 — 请先从 secrets.toml.example 复制并填入 key"
            )
        with SECRETS_PATH.open("rb") as f:
            data = tomllib.load(f)
        configs = data.get("api_configs", [])
        if not 0 <= index < len(configs):
            raise IndexError(
                f"api_configs[{index}] 不存在(共 {len(configs)} 个配置)"
            )
        c = configs[index]
        return cls(
            api_key=c.get("api_key", ""),
            base_url=c.get("base_url", ""),
            model=c.get("model", ""),
            temperature=temperature,
        )

    def meta(self) -> dict:
        """Generation parameters as actually used — logged into every record."""
        return {
            "model": self.model,
            "temperature": self.temperature,
            "base_url": self.base_url,
        }

    # --------- internals ---------

    def _create(self, messages: list[dict], *, n: int = 1, max_retries: int = 3):
        """One chat.completions call with exponential-backoff retry."""
        delay = 1.0
        for attempt in range(max_retries):
            try:
                kwargs: dict = {"n": n} if n > 1 else {}
                return self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    **kwargs,
                )
            except Exception:  # noqa: BLE001
                if attempt == max_retries - 1:
                    raise
                time.sleep(delay)
                delay *= 2

    # --------- public API ---------

    def generate(
        self,
        system: str,
        user: str,
        n: int = 1,
        max_retries: int = 3,
    ) -> list[str]:
        """Return n completions (cleaned of leaked <think> blocks).

        For n>1 the API's `n` parameter is tried once; providers that reject
        it fall back to sequential calls (each with its own retry)."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        outs: list[str] = []
        if n > 1:
            try:
                resp = self._create(messages, n=n, max_retries=1)
                outs = [(c.message.content or "").strip() for c in resp.choices]
            except Exception:  # noqa: BLE001 — provider may not support n>1
                outs = []
        if len(outs) < n:
            outs = []
            for _ in range(n):
                resp = self._create(messages, max_retries=max_retries)
                outs.append((resp.choices[0].message.content or "").strip())
        return [clean_output(o) for o in outs]
