from __future__ import annotations

import difflib
import json
import secrets
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
# title/scenario carry {"ja": ..., "zh": ...} — ja is the formal-study language,
# zh is for the researcher's testing (paper/8 i18n).
_SEED_TOPICS = [
    {
        "title": {"ja": "降りられない満員電車", "zh": "下不去的满员电车"},
        "scenario": {
            "ja": "降りる駅に着いたのに、満員電車の人波に阻まれてドアまでたどり着けない。",
            "zh": "到站了,却被满员电车的人潮堵着,挤不到门口。",
        },
        "shot_count": 3,
        "total_seconds": 15,
    },
    {
        "title": {"ja": "バイト最終日のひとこと", "zh": "打工最后一天的那句话"},
        "scenario": {
            "ja": "アルバイト最後の日。お世話になった店長に伝えたいひとことが、どうしても言えない。",
            "zh": "打工的最后一天,想对照顾过自己的店长说句告别,却怎么也说不出口。",
        },
        "shot_count": 3,
        "total_seconds": 15,
    },
    {
        "title": {"ja": "合格発表の朝", "zh": "放榜的早晨"},
        "scenario": {
            "ja": "合格発表の朝。掲示板の前で、人混みごしに自分の受験番号を探す。",
            "zh": "放榜的早晨,在公告栏前隔着人群寻找自己的考号。",
        },
        "shot_count": 3,
        "total_seconds": 15,
    },
]


def topic_text(topic: dict, field: str, lang: str) -> str:
    """Localized topic field; tolerates legacy plain-str topics."""
    v = topic.get(field, "")
    if isinstance(v, dict):
        return v.get(lang) or v.get("ja") or v.get("zh") or ""
    return v or ""

# Per-round payload, reset at the start of every round.
ROUND_PAYLOAD_DEFAULTS: dict[str, Any] = {
    "r_phase": "intent",          # intent → pipeline → (guidance ⇄) postgen → questionnaire
    "r_intent": "",
    "r_versions": [],             # [{"v", "author": "ai"|"user_edit", "text"}]
    "r_guidance_rounds": [],      # guidance_json["rounds"] (condition E)
    "r_revision_requests": [],    # [{"round", "text"}] (condition D)
    "r_n_ai_rounds": 0,           # D revision rounds / E follow-up guidance rounds
    "r_n_hand_edits": 0,
    "r_hand_edit_chars": 0,
    # guidance working state (condition E)
    "r_g_source": "",             # "fixed3+ai_supplement" | "ai_from_draft"
    "r_g_questions": [],
    "r_g_answers": {},            # {q_idx: {"opt", "custom"}} — persists across
                                  # pagination (Streamlit clears unmounted widgets)
    "r_g_idx": 0,
    "r_g_fallback": False,
    "r_llm_wait": 0.0,
    "r_llm_wait_post": 0.0,       # waits occurring after the first script_shown
    "r_llm_wait_pre": 0.0,        # E round-1: waits inside [guidance_shown, guidance_submit)
    "r_events": [],               # [(epoch_seconds, type)] session mirror for durations
    "r_trial_id": None,
    "r_attempt": "",              # session segment id (LOG4); fresh per round attempt
}

DEFAULTS: dict[str, Any] = {
    "lang": "ja",
    "api_key": "",
    "base_url": "",
    "model": "",
    "api_preset_name": "",
    "researcher_mode": False,
    "researcher_ok": False,
    "participant_id": None,
    "seq": None,
    "stage": "consent",        # consent → screening → rounds → final_survey → done
    "round_idx": 1,
    "round_plan": [],          # [{"condition": str, "topic": dict}] × 3
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
    _attempt_resume()


def _resume_token() -> str:
    """Read the resume token from the URL (?t=…); '' if absent/unavailable."""
    try:
        return (st.query_params.get("t") or "").strip()
    except Exception:  # query params unavailable (e.g. headless AppTest)
        return ""


def _attempt_resume() -> None:
    """Restore an in-progress participant after a refresh/reconnect (AUD6).

    Without this, a wiped session_state sends the subject back to consent and a
    re-screening inserts a *second* passed row → a second Latin-square seq →
    broken balance + inflated sample. We key off an opaque URL token, rebuild the
    round plan from `seq`, and resume at the first round whose questionnaire
    hasn't been submitted yet (an unfinished trial is simply re-done; INSERT OR
    REPLACE keeps it from duplicating)."""
    if st.session_state.get("participant_id"):
        return  # already inside a live session
    p = db.get_participant_by_token(_resume_token())
    if not p or not p.get("passed"):
        return

    def _restore_identity() -> None:
        st.session_state["participant_id"] = p["id"]
        st.session_state["seq"] = p["seq"]
        st.session_state["lang"] = p.get("lang") or st.session_state.get("lang", "ja")

    if p.get("status") == "done":  # finished — restore the completion screen
        _restore_identity()
        st.session_state["stage"] = "done"
        st.session_state["completion_code"] = p.get("completion_code") or ""
        return

    # Need the round plan from here on; bail (don't half-restore) if topics.json
    # has been edited below N_ROUNDS — same guard as begin_rounds.
    topics = load_topics()
    if len(topics) < config.N_ROUNDS:
        return
    _restore_identity()
    st.session_state["round_plan"] = plan_for_seq(p["seq"], topics[: config.N_ROUNDS])
    done_rounds = db.count_questionnaires(p["id"])
    if done_rounds >= config.N_ROUNDS:
        # all rounds answered but status != done → final survey not submitted yet
        st.session_state["round_idx"] = config.N_ROUNDS
        st.session_state["stage"] = "final_survey"
        log_event("session_resumed", {"stage": "final_survey"})
        return
    st.session_state["round_idx"] = done_rounds + 1
    st.session_state["stage"] = "rounds"
    reset_round_payload()
    log_event("session_resumed", {"round_idx": done_rounds + 1})
    # Fresh timing origin: r_events was just wiped, and round_durations anchors
    # t_read_intent / t_total on round_start — without this the resumed round's
    # duration columns land NULL.
    log_event("round_start")


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


def begin_rounds(participant_id: int, seq: int, token: str = "") -> None:
    topics = load_topics()
    if len(topics) < config.N_ROUNDS:
        raise RuntimeError(f"topics.json needs >= {config.N_ROUNDS} topics")
    st.session_state["participant_id"] = participant_id
    st.session_state["seq"] = seq
    st.session_state["stage"] = "rounds"
    st.session_state["round_idx"] = 1
    st.session_state["round_plan"] = plan_for_seq(seq, topics[: config.N_ROUNDS])
    # Put the resume handle in the URL so a refresh/reconnect restores this
    # session instead of re-screening (AUD6). Best-effort: never break the flow.
    if token:
        try:
            st.query_params["t"] = token
        except Exception:
            pass
    reset_round_payload()
    log_event("round_start")


def current_round() -> dict:
    return st.session_state["round_plan"][st.session_state["round_idx"] - 1]


def reset_round_payload() -> None:
    for k, v in ROUND_PAYLOAD_DEFAULTS.items():
        st.session_state[k] = v if not isinstance(v, (dict, list)) else _clone(v)
    # Every (re)start of a round gets its own segment id, so a redone round's
    # events can be told apart from the discarded attempt's (LOG4).
    st.session_state["r_attempt"] = secrets.token_hex(4)
    # Ephemeral widget keys (Streamlit usually cleans these on unmount; pop
    # defensively so a new round never inherits stale editor content).
    for k in list(st.session_state.keys()):
        if k in ("_script_edit", "_intent_input", "_revision_input") or k.startswith("_g_"):
            st.session_state.pop(k, None)


def advance_round() -> None:
    if st.session_state["round_idx"] >= config.N_ROUNDS:
        st.session_state["stage"] = "final_survey"  # whole-study survey before done
        return
    st.session_state["round_idx"] += 1
    reset_round_payload()
    log_event("round_start")


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
    try:  # drop the resume token, else init would re-resume the wiped subject
        st.query_params.clear()
    except Exception:
        pass
    init_state()
    st.session_state.update(keep)


# ---------------- script versions (paper/7 §2: snapshot hard rule) ----------------

def current_script() -> str:
    versions = st.session_state["r_versions"]
    return versions[-1]["text"] if versions else ""


def add_version(text: str, author: str) -> None:
    """Append a script version (author: "ai" | "user_edit") with bookkeeping."""
    versions = st.session_state["r_versions"]
    prev = versions[-1]["text"] if versions else ""
    v = len(versions) + 1
    versions.append({"v": v, "author": author, "text": text})
    if author == "user_edit":
        delta = _edit_chars(prev, text)
        st.session_state["r_n_hand_edits"] += 1
        st.session_state["r_hand_edit_chars"] += delta
        log_event("hand_edit_saved", {"v": v, "chars_delta": delta})
    else:
        log_event("script_shown", {"v": v})


def _edit_chars(a: str, b: str) -> int:
    """Changed-character volume between two versions (difflib opcodes)."""
    total = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag != "equal":
            total += max(i2 - i1, j2 - j1)
    return total


# ---------------- events & timing ----------------

def log_event(type_: str, payload: Optional[dict] = None) -> None:
    st.session_state["r_events"].append((time.time(), type_))
    db.insert_event(
        st.session_state.get("participant_id"),
        st.session_state.get("round_idx"),
        type_,
        payload,
        seq_in_round=len(st.session_state["r_events"]),  # LOG3: 1-based within attempt
        attempt=st.session_state.get("r_attempt") or None,
    )


def add_llm_wait(seconds: float) -> None:
    st.session_state["r_llm_wait"] += seconds
    types = [ty for _, ty in st.session_state["r_events"]]
    if "script_shown" in types:
        st.session_state["r_llm_wait_post"] += seconds
    # E round-1: the final-script generation runs inside the
    # [guidance_shown, guidance_submit) window and lands before the first
    # script_shown, so it escapes r_llm_wait_post. Track it separately so
    # round_durations can keep it out of t_pregen (a creative-time column).
    if "guidance_shown" in types and "guidance_submit" not in types:
        st.session_state["r_llm_wait_pre"] += seconds


def _ts(type_: str, last: bool = False) -> Optional[float]:
    hits = [t for t, ty in st.session_state["r_events"] if ty == type_]
    if not hits:
        return None
    return hits[-1] if last else hits[0]


def round_durations(condition: str) -> dict:
    """Aggregate per-phase durations from the session event mirror.

    Fine-grained timestamps live in the events table; these aggregates are
    convenience columns on the trial row. LLM waits are excluded from the
    creative-time columns (t_pregen / t_postgen)."""
    out = {
        "t_read_intent": _delta("round_start", "intent_submit"),
        "t_pregen": None,
        "t_postgen": None,
        "t_llm_wait": round(st.session_state["r_llm_wait"], 2),
        "t_total": _delta("round_start", "trial_submit"),
    }
    if condition == "E":
        pre = _delta("guidance_shown", "guidance_submit")
        if pre is not None:  # net of the final-script generation wait in-window
            out["t_pregen"] = round(max(0.0, pre - st.session_state["r_llm_wait_pre"]), 2)
    post = _delta("script_shown", "trial_submit")
    if post is not None:
        out["t_postgen"] = round(max(0.0, post - st.session_state["r_llm_wait_post"]), 2)
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
        if isinstance(data, list):
            topics = [_normalize_topic(t) for t in data if isinstance(t, dict)]
            if topics:
                return topics
    except json.JSONDecodeError:
        pass
    return [dict(t) for t in _SEED_TOPICS]


def _int_or(v, default: int) -> int:
    """Dirty legacy topics.json values (CG3) must never crash topic loading."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _normalize_topic(t: dict) -> dict:
    """Back-compat: presets saved before the schema change carried `shot_seconds`
    (per-shot duration). Translate it to the new `total_seconds` (whole clip)."""
    out = dict(t)
    if "total_seconds" not in out and "shot_seconds" in out:
        out["total_seconds"] = _int_or(out.pop("shot_seconds"), 5) * _int_or(out.get("shot_count", 1), 3)
    out["total_seconds"] = _int_or(out.get("total_seconds"), 15)
    out["shot_count"] = _int_or(out.get("shot_count"), 3)
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
