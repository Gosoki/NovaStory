from __future__ import annotations

import json
import random
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
  status TEXT NOT NULL DEFAULT 'in_progress'
);
CREATE TABLE IF NOT EXISTS trials (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL,
  round_idx INTEGER NOT NULL,
  condition TEXT NOT NULL,
  topic_json TEXT,
  intent_statement TEXT,
  ai_outline TEXT,
  edited_outline TEXT,
  dissent_json TEXT,
  adjudication TEXT,
  adjudication_reason TEXT,
  final_output TEXT,
  parse_ok INTEGER,
  regen_count INTEGER DEFAULT 0,
  model TEXT,
  temperature REAL,
  base_url TEXT,
  t_read_intent REAL,
  t_edit REAL,
  t_dissent REAL,
  t_llm_wait REAL,
  t_total REAL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER,
  round_idx INTEGER,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,
  payload_json TEXT
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
  shot_annotations_json TEXT,
  created_at TEXT NOT NULL
);
"""

# Columns added after the first deployment; applied via ALTER on existing DBs.
_MIGRATIONS = [
    "ALTER TABLE questionnaires ADD COLUMN imagine_match INTEGER",
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
) -> tuple[int, Optional[int]]:
    """Insert a participant; returns (id, seq).

    seq (0-8, Latin-square sequence) is assigned at insert time as
    (count of previously passed participants) % 9, None when screened out.
    """
    with _conn() as conn:
        seq: Optional[int] = None
        if passed:
            n = conn.execute(
                "SELECT COUNT(*) FROM participants WHERE passed=1"
            ).fetchone()[0]
            seq = n % 9
        cur = conn.execute(
            "INSERT INTO participants"
            " (created_at, lang, seq, demographics_json, screening_json, passed, status)"
            " VALUES (?,?,?,?,?,?,?)",
            (_now(), lang, seq, _dumps(demographics), _dumps(screening),
             int(passed), "in_progress" if passed else "screened_out"),
        )
        return int(cur.lastrowid), seq


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
        cur = conn.execute(
            f"INSERT INTO trials ({cols}) VALUES ({marks})", tuple(f.values())
        )
        return int(cur.lastrowid)


def insert_event(
    participant_id: Optional[int],
    round_idx: Optional[int],
    type_: str,
    payload: Optional[dict] = None,
) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO events (participant_id, round_idx, ts, type, payload_json)"
            " VALUES (?,?,?,?,?)",
            (participant_id, round_idx, _now(), type_,
             _dumps(payload) if payload else None),
        )


def insert_questionnaire(**f: Any) -> int:
    f.setdefault("created_at", _now())
    cols = ", ".join(f.keys())
    marks = ", ".join("?" for _ in f)
    with _conn() as conn:
        cur = conn.execute(
            f"INSERT INTO questionnaires ({cols}) VALUES ({marks})", tuple(f.values())
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
