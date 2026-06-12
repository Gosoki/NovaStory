from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

import streamlit as st

from core import config, db

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TOPICS_FILE = DATA_DIR / "topics.json"

# 3×3 Latin squares: seq (0-8) = cond_row * 3 + topic_row.
# Condition order and topic→condition pairing are both balanced across 9 seqs.
_COND_ORDERS = (("C", "D", "E"), ("D", "E", "C"), ("E", "C", "D"))
_TOPIC_ORDERS = ((0, 1, 2), (1, 2, 0), (2, 0, 1))

# Seed topics written to data/topics.json on first launch when the file is
# missing. Live session always reads from topics.json via load_topics().
_SEED_TOPICS = [
    {
        "title": "期末周渡劫",
        "scenario": "距离明天早上的专业课期末考试只剩 8 小时,而主角现在才刚打开第一页书。",
        "shot_count": 3,
        "total_seconds": 15,
    },
    {
        "title": "错过的末班车",
        "scenario": "深夜加班结束,主角狂奔到站台时末班车刚好关门开走,只能想办法回家。",
        "shot_count": 3,
        "total_seconds": 15,
    },
    {
        "title": "五分钟的告白",
        "scenario": "毕业典礼散场,人群正在离开,主角只剩五分钟向暗恋的人说出那句话。",
        "shot_count": 3,
        "total_seconds": 15,
    },
]

# Per-round payload, reset at the start of every round.
ROUND_PAYLOAD_DEFAULTS: dict[str, Any] = {
    "r_phase": "intent",       # intent → pipeline → questionnaire
    "r_intent": "",
    "r_outline_ai": "",
    "r_outline_user": "",
    "r_defaults": [],          # pre-sampled default outlines (condition E)
    "r_dissent": "",           # ModeMirror dissent text
    "r_adjudication": "",      # accept / transform / reject
    "r_adjudication_reason": "",
    "r_final": "",
    "r_regen": 0,
    "r_llm_wait": 0.0,
    "r_events": [],            # [(epoch_seconds, type)] session mirror for durations
    "r_trial_id": None,
}

DEFAULTS: dict[str, Any] = {
    "lang": "zh",
    "api_key": "",
    "base_url": "",
    "model": "",
    "api_preset_name": "",
    "researcher_mode": False,
    "researcher_ok": False,
    "participant_id": None,
    "seq": None,
    "stage": "consent",        # consent → screening → rounds → done | screened_out
    "round_idx": 1,
    "round_plan": [],          # [{"condition": str, "topic": dict}] × 3
    "divergence_dim": "",
    "attention_value": None,
    "completion_code": "",
    **ROUND_PAYLOAD_DEFAULTS,
}


def init_state() -> None:
    db.init_db()
    for k, v in DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v if not isinstance(v, (dict, list)) else _clone(v)
    _ensure_api_defaults()


def _clone(v):
    return json.loads(json.dumps(v))


def _ensure_api_defaults() -> None:
    """Auto-apply the first secrets preset so participants never see API config."""
    if (st.session_state.get("api_key") or "").strip():
        return
    cfgs = load_api_configs()
    if cfgs:
        chosen = cfgs[0]
        st.session_state["base_url"] = chosen["base_url"]
        st.session_state["model"] = chosen["model"]
        st.session_state["api_key"] = chosen["api_key"]
        st.session_state["api_preset_name"] = chosen.get("name", "")


# ---------------- assignment & round flow ----------------

def plan_for_seq(seq: int, topics: list[dict]) -> list[dict]:
    conds = _COND_ORDERS[seq // 3]
    topic_idx = _TOPIC_ORDERS[seq % 3]
    return [{"condition": c, "topic": dict(topics[i])} for c, i in zip(conds, topic_idx)]


def begin_rounds(participant_id: int, seq: int) -> None:
    topics = load_topics()
    if len(topics) < config.N_ROUNDS:
        raise RuntimeError(f"topics.json needs >= {config.N_ROUNDS} topics")
    st.session_state["participant_id"] = participant_id
    st.session_state["seq"] = seq
    st.session_state["stage"] = "rounds"
    st.session_state["round_idx"] = 1
    st.session_state["round_plan"] = plan_for_seq(seq, topics[: config.N_ROUNDS])
    st.session_state["divergence_dim"] = config.DIVERGENCE_POOL[
        seq % len(config.DIVERGENCE_POOL)
    ]
    reset_round_payload()
    log_event("round_start")


def current_round() -> dict:
    return st.session_state["round_plan"][st.session_state["round_idx"] - 1]


def reset_round_payload() -> None:
    for k, v in ROUND_PAYLOAD_DEFAULTS.items():
        st.session_state[k] = v if not isinstance(v, (dict, list)) else _clone(v)
    # Ephemeral widget keys (Streamlit usually cleans these on unmount; pop
    # defensively so a new round never inherits stale editor content).
    for k in ("_outline_edit", "_intent_input", "_mm_reason", "_mm_choice"):
        st.session_state.pop(k, None)


def advance_round() -> None:
    if st.session_state["round_idx"] >= config.N_ROUNDS:
        st.session_state["stage"] = "done"
        return
    st.session_state["round_idx"] += 1
    reset_round_payload()
    log_event("round_start")


def is_last_round() -> bool:
    return st.session_state["round_idx"] >= config.N_ROUNDS


def reset_for_next() -> None:
    """Local-testing convenience (researcher only): wipe the subject, keep config."""
    keep = {
        k: st.session_state.get(k)
        for k in (
            "lang", "api_key", "base_url", "model", "api_preset_name",
            "researcher_mode", "researcher_ok",
        )
    }
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    init_state()
    st.session_state.update(keep)


# ---------------- events & timing ----------------

def log_event(type_: str, payload: Optional[dict] = None) -> None:
    st.session_state["r_events"].append((time.time(), type_))
    db.insert_event(
        st.session_state.get("participant_id"),
        st.session_state.get("round_idx"),
        type_,
        payload,
    )


def add_llm_wait(seconds: float) -> None:
    st.session_state["r_llm_wait"] += seconds


def _ts(type_: str, last: bool = False) -> Optional[float]:
    hits = [t for t, ty in st.session_state["r_events"] if ty == type_]
    if not hits:
        return None
    return hits[-1] if last else hits[0]


def round_durations(condition: str) -> dict:
    """Aggregate per-phase durations from the session event mirror.

    Fine-grained timestamps live in the events table; these aggregates are
    convenience columns on the trial row. LLM streaming wait is tracked
    separately (r_llm_wait) and excluded from creative-time interpretation.
    """
    out = {
        "t_read_intent": _delta("round_start", "intent_submit"),
        "t_edit": None,
        "t_dissent": None,
        "t_llm_wait": round(st.session_state["r_llm_wait"], 2),
        "t_total": _delta("round_start", "trial_submit"),
    }
    if condition == "D":
        out["t_edit"] = _delta("outline_shown", "final_click", last_a=True)
    elif condition == "E":
        edit1 = _delta("outline_shown", "dissent_request", last_a=True)
        edit2 = _delta("adjudicate", "final_click")
        if edit1 is not None or edit2 is not None:
            out["t_edit"] = round((edit1 or 0.0) + (edit2 or 0.0), 2)
        out["t_dissent"] = _delta("dissent_shown", "adjudicate")
    return out


def _delta(a: str, b: str, last_a: bool = False) -> Optional[float]:
    ta, tb = _ts(a, last=last_a), _ts(b)
    if ta is None or tb is None or tb < ta:
        return None
    return round(tb - ta, 2)


# ---------------- topics.json -----------------

def load_topics() -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TOPICS_FILE.exists():
        TOPICS_FILE.write_text(
            json.dumps(_SEED_TOPICS, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return [dict(t) for t in _SEED_TOPICS]
    try:
        data = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            return [_normalize_topic(t) for t in data]
    except json.JSONDecodeError:
        pass
    return [dict(t) for t in _SEED_TOPICS]


def _normalize_topic(t: dict) -> dict:
    """Back-compat: presets saved before the schema change carried `shot_seconds`
    (per-shot duration). Translate it to the new `total_seconds` (whole clip)."""
    out = dict(t)
    if "total_seconds" not in out and "shot_seconds" in out:
        out["total_seconds"] = int(out.pop("shot_seconds")) * int(out.get("shot_count", 1))
    out.setdefault("total_seconds", 15)
    out.setdefault("shot_count", 3)
    return out


def save_topic_preset(topic: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    topics = load_topics()
    for t in topics:
        if t.get("title") == topic.get("title") and t.get("scenario") == topic.get("scenario"):
            return
    topics.append(topic)
    TOPICS_FILE.write_text(
        json.dumps(topics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
