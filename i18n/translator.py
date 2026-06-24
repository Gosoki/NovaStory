import json
import logging
from functools import lru_cache
from pathlib import Path

import streamlit as st

LOCALES_DIR = Path(__file__).parent / "locales"
# ja = formal-study language (participants are Japanese); zh kept for the
# researcher's testing. Missing keys fall back to ja so a participant never
# sees a Chinese string. AVAILABLE_LANGS ordered ja-first for the picker.
DEFAULT_LANG = "ja"
AVAILABLE_LANGS = ["ja", "zh", "en"]
LANG_LABELS = {"zh": "中文", "en": "English", "ja": "日本語"}

_log = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def _load(lang: str) -> dict:
    path = LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        _log.warning("locale file missing: %s", path)
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_lang() -> str:
    return st.session_state.get("lang", DEFAULT_LANG)


def _dig(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def t(key: str, **kwargs) -> str:
    lang = get_lang()
    val = _dig(_load(lang), key)
    if val is None and lang != DEFAULT_LANG:
        val = _dig(_load(DEFAULT_LANG), key)
        if val is not None:
            _log.warning("missing key %r in %s, fell back to %s", key, lang, DEFAULT_LANG)
    if val is None:
        return key
    if kwargs:
        try:
            return val.format(**kwargs)
        except (KeyError, IndexError):
            return val
    return val
