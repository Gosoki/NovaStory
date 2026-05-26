from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TOPICS_FILE = DATA_DIR / "topics.json"

STEP_ORDER = ["A", "B", "C", "D", "DONE"]

# Seed topic written to data/topics.json on first launch when the file is missing.
# Live session always reads the topic from topics.json via load_topics() — never
# from this constant — so editing this changes only what a fresh install starts with.
_SEED_TOPIC = {
    "title": "期末周渡劫",
    "scenario": "距离明天早上的专业课期末考试只剩 8 小时，而主角现在才刚打开第一页书。",
    "shot_count": 3,
    "total_seconds": 15,
}

DEFAULTS: dict[str, Any] = {
    "lang": "zh",
    "subject_id": "",
    "flow": "ABC",
    "api_key": "",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "topic_frozen": False,
    "researcher_mode": False,
    "subject_started": False,
    "step": "A",
    "group_start_ts": {},
    "group_submitted": {"A": False, "B": False, "C": False, "D": False},
    # per-group payloads
    "a_script": "",
    "a_seed": "",
    "b_seed": "",
    "b_prompt": "",
    "b_output": "",
    "c_output": "",
    "d_outline_ai": "",
    "d_outline_user": "",
    "d_final": "",
}


def init_state() -> None:
    for k, v in DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v if not isinstance(v, (dict, list)) else _clone(v)
    # Topic is sourced from topics.json (single source of truth). Loaded lazily so
    # researcher edits in the sidebar always sync with the file.
    if "topic" not in st.session_state:
        st.session_state["topic"] = dict(load_topics()[0])


def _clone(v):
    return json.loads(json.dumps(v))


def reset_subject() -> None:
    """Reset everything tied to the current subject; keep researcher-side config.

    Notes:
    - `flow` is NOT kept — each subject picks their assignment on the onboarding screen.
    - `_api_preset_idx` is kept so the researcher doesn't have to re-pick the endpoint.
    """
    keep = {
        "lang": st.session_state.get("lang", DEFAULTS["lang"]),
        "api_key": st.session_state.get("api_key", ""),
        "base_url": st.session_state.get("base_url", DEFAULTS["base_url"]),
        "model": st.session_state.get("model", DEFAULTS["model"]),
        "topic": _clone(st.session_state.get("topic", _SEED_TOPIC)),
        "researcher_mode": st.session_state.get("researcher_mode", False),
        "_api_preset_idx": st.session_state.get("_api_preset_idx", 0),
    }
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    init_state()
    for k, v in keep.items():
        st.session_state[k] = v


def ensure_start_ts(group: str) -> None:
    """Stamp the first time the subject views this group; never reset on rerun."""
    ts = st.session_state["group_start_ts"]
    if group not in ts:
        ts[group] = time.time()


def elapsed_seconds(group: str) -> float:
    start = st.session_state["group_start_ts"].get(group)
    if not start:
        return 0.0
    return round(time.time() - start, 2)


def can_view(group: str) -> bool:
    """A is always viewable; later groups require previous submission AND matching flow."""
    if group == "A":
        return True
    if group == "B":
        return st.session_state["group_submitted"].get("A", False)
    if group == "C":
        return (
            st.session_state["flow"] == "ABC"
            and st.session_state["group_submitted"].get("B", False)
        )
    if group == "D":
        return (
            st.session_state["flow"] == "ABD"
            and st.session_state["group_submitted"].get("B", False)
        )
    return False


def mark_submitted(group: str) -> None:
    st.session_state["group_submitted"][group] = True
    # Advance step pointer
    flow = st.session_state["flow"]
    if group == "A":
        st.session_state["step"] = "B"
    elif group == "B":
        st.session_state["step"] = "C" if flow == "ABC" else "D"
    elif group in ("C", "D"):
        st.session_state["step"] = "DONE"


def is_done() -> bool:
    return st.session_state.get("step") == "DONE"


def freeze_topic() -> None:
    """Lock topic edits once subject has started (called when A is first viewed)."""
    st.session_state["topic_frozen"] = True


# ---------------- topics.json -----------------

def load_topics() -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TOPICS_FILE.exists():
        TOPICS_FILE.write_text(
            json.dumps([_SEED_TOPIC], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return [dict(_SEED_TOPIC)]
    try:
        data = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            return [_normalize_topic(t) for t in data]
    except json.JSONDecodeError:
        pass
    return [dict(_SEED_TOPIC)]


def _normalize_topic(t: dict) -> dict:
    """Back-compat: presets saved before the schema change carried `shot_seconds`
    (per-shot duration). Translate it to the new `total_seconds` (whole clip)."""
    out = dict(t)
    if "total_seconds" not in out and "shot_seconds" in out:
        out["total_seconds"] = int(out.pop("shot_seconds")) * int(out.get("shot_count", 1))
    out.setdefault("total_seconds", 15)
    out.setdefault("shot_count", 3)
    return out


# ---------------- api configs (from .streamlit/secrets.toml) -----------------

def load_api_configs() -> list[dict]:
    """Read [[api_configs]] entries from .streamlit/secrets.toml.

    Each entry should have keys: name, base_url, model, api_key.
    Returns [] if secrets file is missing or empty — sidebar handles that case.
    """
    try:
        raw = st.secrets.get("api_configs", [])
    except Exception:  # FileNotFoundError, StreamlitSecretNotFoundError, etc.
        return []
    out = []
    for item in raw:
        out.append({
            "name": item.get("name", "(unnamed)"),
            "base_url": item.get("base_url", ""),
            "model": item.get("model", ""),
            "api_key": item.get("api_key", ""),
        })
    return out


def save_topic_preset(topic: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    topics = load_topics()
    # avoid exact duplicates by title+scenario
    for t in topics:
        if t.get("title") == topic.get("title") and t.get("scenario") == topic.get("scenario"):
            return
    topics.append(topic)
    TOPICS_FILE.write_text(
        json.dumps(topics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
