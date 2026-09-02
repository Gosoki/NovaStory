from __future__ import annotations

import difflib
import json
import secrets
import time
from pathlib import Path
from typing import Any, Optional

import streamlit as st

from core import config, db
from i18n import AVAILABLE_LANGS

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TOPICS_FILE = DATA_DIR / "topics.json"

# seq (0-17) = cond_row * 3 + topic_row.
# _COND_ORDERS = all 6 permutations of C/D/E (a Williams design): each condition
# is immediately preceded by each other condition equally often, so first-order
# carryover is balanced and the E−D contrast is not confounded with order (the 3
# cyclic orders CDE/DEC/ECD used before had D always after C, E always after D —
# deep-review 2026-07-19 #1). Each condition also sits in each round-position
# exactly twice. _TOPIC_ORDERS (3 rotations) balances condition×topic and
# topic×position. 6 × 3 = 18 seqs; N=36 → 2 completers each.
_COND_ORDERS = (
    ("C", "D", "E"), ("C", "E", "D"),
    ("D", "C", "E"), ("D", "E", "C"),
    ("E", "C", "D"), ("E", "D", "C"),
)
_TOPIC_ORDERS = ((0, 1, 2), (1, 2, 0), (2, 0, 1))
assert len(_COND_ORDERS) * len(_TOPIC_ORDERS) == config.LATIN_SQUARE_N

# Seed topics written to data/topics.json on first launch when the file is
# missing. Live session always reads from topics.json via load_topics().
# title/scenario/choices carry {"ja": ..., "zh": ...} — ja is the formal-study
# language, zh is for the researcher's testing (paper/8 i18n).
# `scenario` is the setup; `choices` is the "which will you do?" list, kept
# separate since 2026-09-01 (§15) so the UI can grey it out and mark it as one
# suggestion among many rather than a required direction. The model still gets
# both, joined (prompts.scenario_text).
_SEED_TOPICS = [
    {
        "title": {"ja": "拾った切符（誰かの落とし物）", "zh": "捡到的失物（别人掉的东西）"},
        "scenario": {
            "ja": "道で、誰かが落とした小さな物を拾う。",
            "zh": "在路上捡到别人掉落的小东西。",
        },
        "choices": {
            "ja": "中を見るか、届けるか、それとも——。",
            "zh": "是打开看看、拿去归还,还是——。",
        },
        "shot_count": 3,
        "total_seconds": 15,
    },
    {
        "title": {"ja": "最後のひと口（分け合う/独り占め）", "zh": "最后一口（分享还是独享）"},
        "scenario": {
            "ja": "目の前に、好きなものがたったひとつだけ残っている。ほんの数秒の攻防。",
            "zh": "眼前只剩最后一口喜欢的东西——短短几秒的拉锯。",
        },
        "choices": {
            "ja": "誰かと分けるか、自分で食べるか。",
            "zh": "是分给别人,还是自己吃掉。",
        },
        "shot_count": 3,
        "total_seconds": 15,
    },
    {
        "title": {"ja": "はじめての街の、最初の一歩", "zh": "陌生街道的第一步"},
        "scenario": {
            "ja": "見慣れない街に降り立った、最初の一歩。",
            "zh": "降落在陌生的街道,迈出第一步。",
        },
        "choices": {
            "ja": "地図を見るか、匂いをたどるか、誰かに声をかけるか。",
            "zh": "是看地图、循着气味走,还是开口问路。",
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
    # Opaque id for this browser session, minted before a participant row
    # exists so the intake stage (consent → intro → screening) can log events
    # and have them backfilled with the participant id at screening.
    "session_id": "",
    "api_key": "",
    "base_url": "",
    "model": "",
    "api_preset_name": "",
    "researcher_mode": False,
    "researcher_ok": False,
    "participant_id": None,
    "seq": None,
    "stage": "consent",        # consent → screening → intro → rounds → final_survey → done
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
    if not st.session_state["session_id"]:
        st.session_state["session_id"] = secrets.token_hex(4)
    _apply_url_lang()
    _ensure_api_defaults()
    _attempt_resume()


def _apply_url_lang() -> None:
    """`?lang=ja|zh|en` preselects the session language (testing / recruitment aid).

    Applied ONCE per browser session and only before a participant row exists,
    so it behaves exactly like arriving on the consent page with the picker
    already set: the subject can still change it there, and nothing can flip the
    language mid-study (JP6). An unknown value is ignored, leaving the ja
    default. The pick lands on `participants.lang`, so monitor_panel's
    language-mix warning still catches a session run in the wrong language."""
    if st.session_state.get("_url_lang_done"):
        return
    st.session_state["_url_lang_done"] = True
    if st.session_state.get("participant_id"):
        return
    try:
        v = (st.query_params.get("lang") or "").strip().lower()
    except Exception:  # query params unavailable (e.g. headless AppTest)
        return
    if v in AVAILABLE_LANGS:
        st.session_state["lang"] = v


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
    if done_rounds == 0:
        # Round 1 isn't finished, so they were either still on the briefing page
        # or partway through round 1 — and a resume wipes the round payload
        # either way, i.e. round 1 restarts from the intent step regardless.
        # Land on the briefing rather than in the round: someone who refreshed
        # ON the briefing would otherwise silently lose the standardized
        # onboarding, and its button is what starts the round clock (§4).
        st.session_state["stage"] = "intro"
        # Mint a fresh attempt segment here too, not only in begin_rounds: two
        # tabs resumed from the same token must be tellable apart in the event
        # log from their very first event (LOG4), and `session_resumed` is it.
        reset_round_payload()
        log_event("session_resumed", {"stage": "intro"})
        return
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


def enter_intro(participant_id: int, seq: int, token: str = "") -> None:
    """Screening is in; park the participant on the how-it-works page (§4).

    Identity and the resume token are installed NOW — a refresh on the briefing
    page must resume, not re-screen (which would insert a second passed row and
    burn a second Latin-square seq). What is deliberately NOT done here is
    `round_start`: t_read_intent is measured from it, so starting the clock
    before the briefing would fold the whole briefing into round 1's reading
    time. begin_rounds (called by the intro page's button) starts it."""
    topics = load_topics()
    if len(topics) < config.N_ROUNDS:
        raise RuntimeError(f"topics.json needs >= {config.N_ROUNDS} topics")
    st.session_state["participant_id"] = participant_id
    st.session_state["seq"] = seq
    st.session_state["stage"] = "intro"
    st.session_state["round_idx"] = 1
    st.session_state["round_plan"] = plan_for_seq(seq, topics[: config.N_ROUNDS])
    if token:
        try:
            st.query_params["t"] = token
        except Exception:
            pass


def start_rounds() -> None:
    """离开说明页,进入第 1 轮。

    刻意**不**重建 round_plan:计划在 `enter_intro` 里就装好了,而这颗按钮被按下时
    被试行与拉丁方 seq 早已落库。若在这里再读一次 topics.json,只要研究员在被试读
    说明页的这两分钟里动了题库(哪怕只是存了个半截文件),`begin_rounds` 就会抛
    RuntimeError —— 抛在一个已经吃掉一个 seq、又没有任何出路的被试脸上。"""
    st.session_state["stage"] = "rounds"
    st.session_state["round_idx"] = 1
    reset_round_payload()
    log_event("round_start")


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

def log_event(type_: str, payload: Optional[dict] = None, *, once: bool = False) -> None:
    """Round-scoped event. `once=True` records at most one per round attempt —
    Streamlit re-runs the whole script on every interaction, so an unguarded
    page-view log would fire once per keystroke. `r_events` is the per-attempt
    mirror, so a redone round logs its own copy."""
    if once and any(ty == type_ for _, ty in st.session_state["r_events"]):
        return
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


def log_intake_event(type_: str, payload: Optional[dict] = None) -> None:
    """Intake-stage event (consent → intro → screening), logged BEFORE a
    participant row exists — that stage is the one part of the study with no
    timing data otherwise, and "did they actually read the how-it-works page"
    is a data-quality question.

    Written at `round_idx=0` so it never lands in a round's event stream, keyed
    by `session_id` in `attempt`; `db.attach_intake_events` backfills the
    participant id at screening. Each type is recorded at most once per session
    (these are one-shot page views, and Streamlit re-runs on every keystroke)."""
    seen = st.session_state.setdefault("_intake_seen", [])
    if type_ in seen:
        return
    seen.append(type_)
    db.insert_event(
        st.session_state.get("participant_id"),
        0,
        type_,
        payload,
        seq_in_round=len(seen),
        attempt=st.session_state.get("session_id") or None,
    )


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
