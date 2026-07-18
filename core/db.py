from __future__ import annotations

import json
import random
import secrets
import sqlite3
import string
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "novastory.db"

TABLES = ("participants", "trials", "events", "questionnaires")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  lang TEXT,
  seq INTEGER,
  demographics_json TEXT,
  screening_json TEXT,
  passed INTEGER NOT NULL DEFAULT 0,
  attention_ok INTEGER,
  completion_code TEXT,
  token TEXT,                 -- opaque resume handle (URL ?t=), not the row id
  final_survey_json TEXT,     -- whole-study survey, shown after all rounds
  status TEXT NOT NULL DEFAULT 'in_progress'
);
CREATE TABLE IF NOT EXISTS trials (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL,
  round_idx INTEGER NOT NULL,
  condition TEXT NOT NULL,
  topic_json TEXT,
  intent_statement TEXT,
  final_output TEXT,
  parse_ok INTEGER,
  regen_count INTEGER DEFAULT 0,
  model TEXT,
  temperature REAL,
  base_url TEXT,
  t_read_intent REAL,
  t_llm_wait REAL,
  t_total REAL,
  guidance_json TEXT,         -- E: rounds of option-style Q&A (paper/7 §6)
  revision_requests TEXT,     -- D: [{round, text}]
  script_versions TEXT,       -- [{v, author: "ai"|"user_edit", text}]
  n_ai_rounds INTEGER,        -- D revision rounds / E follow-up guidance rounds
  n_hand_edits INTEGER,
  hand_edit_chars INTEGER,
  t_pregen REAL,              -- E: round-1 answering net time
  t_postgen REAL,             -- first script shown -> submit, net of LLM waits
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER,
  round_idx INTEGER,
  ts TEXT NOT NULL,              -- millisecond ISO since 2026-07-02 (LOG3)
  type TEXT NOT NULL,
  payload_json TEXT,
  seq_in_round INTEGER,          -- 1-based order within the round attempt (LOG3)
  attempt TEXT,                  -- session segment id; a redo round gets a new one (LOG4)
  trial_id INTEGER               -- backfilled at trial submit for the winning attempt (LOG4)
);
CREATE TABLE IF NOT EXISTS questionnaires (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL,
  round_idx INTEGER NOT NULL,
  ownership_json TEXT,
  soa_json TEXT,
  tlx_json TEXT,
  intent_violation INTEGER,
  imagine_match INTEGER,
  satisfaction INTEGER,
  ai_q_quality INTEGER,
  ai_q_amount INTEGER,          -- E only: 1=too few · 4=just right · 7=too many
  ai_q_best_json TEXT,          -- E only: the guiding question flagged most useful (optional)
  shot_annotations_json TEXT,
  created_at TEXT NOT NULL
);
"""

# One row per (participant, round): a resume re-doing a round overwrites the
# orphan trial/questionnaire instead of duplicating it (see INSERT OR REPLACE).
_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_trials_pid_round ON trials(participant_id, round_idx)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_quest_pid_round ON questionnaires(participant_id, round_idx)",
]

# Columns added after the first deployment; applied via ALTER on existing DBs.
_MIGRATIONS = [
    "ALTER TABLE questionnaires ADD COLUMN imagine_match INTEGER",
    "ALTER TABLE questionnaires ADD COLUMN satisfaction INTEGER",
    "ALTER TABLE questionnaires ADD COLUMN ai_q_quality INTEGER",
    "ALTER TABLE questionnaires ADD COLUMN ai_q_amount INTEGER",
    "ALTER TABLE questionnaires ADD COLUMN ai_q_best_json TEXT",
    "ALTER TABLE participants ADD COLUMN token TEXT",
    "ALTER TABLE participants ADD COLUMN final_survey_json TEXT",
    # v3 (guided co-creation) trial columns
    "ALTER TABLE trials ADD COLUMN guidance_json TEXT",
    "ALTER TABLE trials ADD COLUMN revision_requests TEXT",
    "ALTER TABLE trials ADD COLUMN script_versions TEXT",
    "ALTER TABLE trials ADD COLUMN n_ai_rounds INTEGER",
    "ALTER TABLE trials ADD COLUMN n_hand_edits INTEGER",
    "ALTER TABLE trials ADD COLUMN hand_edit_chars INTEGER",
    "ALTER TABLE trials ADD COLUMN t_pregen REAL",
    "ALTER TABLE trials ADD COLUMN t_postgen REAL",
    # detailed-log batch (LOG3/LOG4, 2026-07-02)
    "ALTER TABLE events ADD COLUMN seq_in_round INTEGER",
    "ALTER TABLE events ADD COLUMN attempt TEXT",
    "ALTER TABLE events ADD COLUMN trial_id INTEGER",
]


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        for stmt in _INDEXES:
            try:
                conn.execute(stmt)
            except sqlite3.IntegrityError:
                pass  # legacy dev DB already has duplicate (pid, round) rows


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


# ---------------- participants ----------------

def insert_participant(
    lang: str,
    demographics: dict,
    screening: dict,
    passed: bool,
) -> tuple[int, Optional[int], str]:
    """Insert a participant; returns (id, seq, token).

    seq (0-8, Latin-square sequence) is assigned at insert time as
    (count of previously passed participants) % 9, None when screened out.
    token is an opaque resume handle put in the URL (?t=) so a refresh/reconnect
    restores the session instead of re-screening (which would consume a 2nd seq).
    """
    token = secrets.token_urlsafe(9)
    with _conn() as conn:
        seq: Optional[int] = None
        if passed:
            # CG1: take the write lock BEFORE counting, so two participants
            # passing screening in the same instant can't both read the same
            # count and grab the same Latin-square seq.
            conn.execute("BEGIN IMMEDIATE")
            # Researcher-injected test subjects (screening_json {"dev": true})
            # must not shift real participants' Latin-square rotation.
            n = conn.execute(
                "SELECT COUNT(*) FROM participants WHERE passed=1"
                " AND COALESCE(json_extract(screening_json, '$.dev'), 0) != 1"
            ).fetchone()[0]
            seq = n % 9
        cur = conn.execute(
            "INSERT INTO participants"
            " (created_at, lang, seq, demographics_json, screening_json, passed, token, status)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (_now(), lang, seq, _dumps(demographics), _dumps(screening),
             int(passed), token, "in_progress" if passed else "screened_out"),
        )
        return int(cur.lastrowid), seq, token


def get_participant_by_token(token: str) -> Optional[dict]:
    """Look up a participant by their resume token; None if not found."""
    if not token:
        return None
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM participants WHERE token=? LIMIT 1", (token,)
        ).fetchone()
        return dict(row) if row else None


def count_questionnaires(participant_id: int) -> int:
    """How many rounds this participant has fully completed (a round is done once
    its questionnaire is submitted) — used to resume at the right round."""
    with _conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM questionnaires WHERE participant_id=?",
            (participant_id,),
        ).fetchone()[0]


def update_participant(pid: int, **fields: Any) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    with _conn() as conn:
        conn.execute(
            f"UPDATE participants SET {cols} WHERE id=?", (*fields.values(), pid)
        )


def make_completion_code(pid: int) -> str:
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    update_participant(pid, completion_code=code, status="done")
    return code


# ---------------- trials / events / questionnaires ----------------

def insert_trial(**f: Any) -> int:
    f.setdefault("created_at", _now())
    cols = ", ".join(f.keys())
    marks = ", ".join("?" for _ in f)
    with _conn() as conn:
        # OR REPLACE: a resume re-doing a round (or a double-click) overwrites the
        # existing (participant_id, round_idx) row instead of duplicating it.
        cur = conn.execute(
            f"INSERT OR REPLACE INTO trials ({cols}) VALUES ({marks})", tuple(f.values())
        )
        return int(cur.lastrowid)


def insert_event(
    participant_id: Optional[int],
    round_idx: Optional[int],
    type_: str,
    payload: Optional[dict] = None,
    seq_in_round: Optional[int] = None,
    attempt: Optional[str] = None,
) -> None:
    ts = datetime.now().isoformat(timespec="milliseconds")  # LOG3: sub-second gaps matter
    with _conn() as conn:
        conn.execute(
            "INSERT INTO events (participant_id, round_idx, ts, type, payload_json,"
            " seq_in_round, attempt) VALUES (?,?,?,?,?,?,?)",
            (participant_id, round_idx, ts, type_,
             _dumps(payload) if payload else None, seq_in_round, attempt),
        )


def attach_trial_to_events(
    trial_id: int, participant_id: int, round_idx: int, attempt: Optional[str]
) -> None:
    """LOG4: after a trial lands, stamp its id onto the events of the attempt
    that produced it — a redone round's stale attempts stay trial_id NULL.
    A redo does INSERT OR REPLACE → a *new* trial id, so first clear any trial_id
    left on OTHER attempts of this round; otherwise their events would dangle at a
    now-deleted trial id (they keep their own `attempt` segment id for lineage)."""
    if not attempt:
        return
    with _conn() as conn:
        conn.execute(
            "UPDATE events SET trial_id=NULL WHERE participant_id=? AND round_idx=?"
            " AND (attempt IS NULL OR attempt!=?)",
            (participant_id, round_idx, attempt),
        )
        conn.execute(
            "UPDATE events SET trial_id=? WHERE participant_id=? AND round_idx=?"
            " AND attempt=?",
            (trial_id, participant_id, round_idx, attempt),
        )


def insert_questionnaire(**f: Any) -> int:
    f.setdefault("created_at", _now())
    cols = ", ".join(f.keys())
    marks = ", ".join("?" for _ in f)
    with _conn() as conn:
        cur = conn.execute(
            f"INSERT OR REPLACE INTO questionnaires ({cols}) VALUES ({marks})",
            tuple(f.values()),
        )
        return int(cur.lastrowid)


# ---------------- researcher ----------------

def load_table(name: str) -> pd.DataFrame:
    if name not in TABLES:
        raise ValueError(f"unknown table: {name}")
    with _conn() as conn:
        return pd.read_sql_query(f"SELECT * FROM {name}", conn)


def counts() -> dict:
    with _conn() as conn:
        return {
            "participants": conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0],
            "passed": conn.execute("SELECT COUNT(*) FROM participants WHERE passed=1").fetchone()[0],
            "done": conn.execute("SELECT COUNT(*) FROM participants WHERE status='done'").fetchone()[0],
            "trials": conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0],
        }
