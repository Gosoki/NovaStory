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


def _lookup_dotted(d: dict, dotted: str):
    """按点号路径逐层取值;任一层缺失或不是 dict → None(命名空间前缀会返回 dict)。"""
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def t(key: str, **kwargs) -> str:
    lang = get_lang()
    val = _lookup_dotted(_load(lang), key)
    if val is None and lang != DEFAULT_LANG:
        val = _lookup_dotted(_load(DEFAULT_LANG), key)
        if val is not None:
            _log.warning("missing key %r in %s, fell back to %s", key, lang, DEFAULT_LANG)
    if val is None:
        return key
    if kwargs:
        try:
            return val.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            # ValueError = 文案里有孤立的 { 或 }:宁可原样显示也别把整页炸掉
            return val
    return val
