from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from core import config

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "novastory.db"

TABLES = ("participants", "trials", "events", "questionnaires", "trials_archive")

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
  attention_raw INTEGER,        -- raw response to the attention item (careless-check, #31)
  completion_code TEXT,
  token TEXT,                 -- opaque resume handle (URL ?t=), not the row id
  final_survey_json TEXT,     -- whole-study survey, shown after all rounds
  finished_at TEXT,           -- set when the completion code is issued; created_at→here = whole-session time
  contact_json TEXT,          -- OPT-IN contact left on the done page: {email, want_video, note, at}
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
CREATE TABLE IF NOT EXISTS trials_archive (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL,
  round_idx INTEGER NOT NULL,
  archived_at TEXT NOT NULL,
  row_json TEXT NOT NULL            -- 被 INSERT OR REPLACE 顶掉的那一行 trials(整行 JSON)
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
  trial_id INTEGER,             -- 这份问卷评的是哪一行 trial(另一会话重做过这一轮时能对出来)
  attempt TEXT,                 -- 同上,段 id(LOG4)
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
    "ALTER TABLE participants ADD COLUMN attention_raw INTEGER",
    "ALTER TABLE participants ADD COLUMN token TEXT",
    "ALTER TABLE participants ADD COLUMN final_survey_json TEXT",
    "ALTER TABLE participants ADD COLUMN finished_at TEXT",
    "ALTER TABLE participants ADD COLUMN contact_json TEXT",
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
    # 2026-09-02:问卷 ↔ 终稿的对应关系以前只靠 (pid, round) 隐含;两个会话各自 INSERT OR
    # REPLACE 之后,问卷评的可能不是现行那份终稿,而分析侧无从察觉。
    "ALTER TABLE questionnaires ADD COLUMN trial_id INTEGER",
    "ALTER TABLE questionnaires ADD COLUMN attempt TEXT",
    # 采数期间改代码是被预期的(冻结允许偏差记录),可数据里没有一列说「这一行是哪个版本产生的」
    "ALTER TABLE trials ADD COLUMN app_rev TEXT",
]


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout=30 已经是 sqlite3_busy_timeout(30s);以前紧接着又 PRAGMA busy_timeout=10000
    # 把它悄悄覆盖成 10s —— 两处写两个数,读者不知道哪个生效。只留一处。
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# 每个进程对同一个库只需初始化一次:init_state 每次 rerun 都调 init_db,以前每次都跑整份
# DDL + 21 条注定失败的 ALTER。按路径记录,测试脚本切换 DB_PATH 后照样会初始化新库;
# 文件被删掉重建也会重新初始化。
_INITIALIZED: set[Path] = set()


def init_db() -> None:
    if DB_PATH in _INITIALIZED and DB_PATH.exists():
        return
    with _conn() as conn:
        conn.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise   # 只吞「列已存在」;库文件损坏/只读之类的真错误不该被同一句 pass 掩盖
        for stmt in _INDEXES:
            try:
                conn.execute(stmt)
            except sqlite3.IntegrityError:
                pass  # legacy dev DB already has duplicate (pid, round) rows
        # 索引建不起来(库里已有重复 (pid, round) 行)以前是静默的:整个采数期都会在无索引状态
        # 运行,INSERT OR REPLACE 退化成普通 INSERT,重做轮产生重复行、续接少做一轮。宁可拒绝启动。
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        missing = [n for n in ("idx_trials_pid_round", "idx_quest_pid_round") if n not in have]
        if missing:
            raise RuntimeError(
                f"unique index missing: {missing} — {DB_PATH} 里已有重复 (participant_id, round_idx) 行,"
                " 先归档/清理这个库再启动")
    _INITIALIZED.add(DB_PATH)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _cols(fields) -> str:
    """把 **kwargs 的键拼成 SQL 列名清单。键只该来自内部调用方,但这几个函数长得像
    「随手加一列就能用」,所以拦住任何不是 Python 标识符的键,f-string 拼 SQL 才不会变成注入口。"""
    bad = [k for k in fields if not str(k).isidentifier()]
    if bad:
        raise ValueError(f"illegal column name(s): {bad}")
    return ", ".join(fields)


# ---------------- participants ----------------

def insert_participant(
    lang: str,
    demographics: dict,
    screening: dict,
    passed: bool,
) -> tuple[int, Optional[int], str]:
    """Insert a participant; returns (id, seq, token).

    seq (0..LATIN_SQUARE_N-1, i.e. 0-17, a Williams-design sequence) is assigned at
    insert time as (count of previously passed non-dev participants) % LATIN_SQUARE_N (18),
    None when screened out.
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
            # json_valid 先行:一行非法 JSON(手改库 / 写入中断)会让 json_extract 抛错,
            # 整条 COUNT 崩 → 阻断之后**所有**新被试入库。非法 JSON 的行按真被试计。
            # 取**当前用得最少**的那个格子(平局取编号小的),而不是「总人数 % 18」。
            # 两者在没有空缺时逐个等价:空库→0、1 人后→1、…、18 人后→0,轮转完全一样。
            # 区别只在有人被释放之后(monitor 面板的「释放」把行标成 dev,不再计入):
            #   · 旧的计数法:13 人占了 0-12,释放掉 seq=5 那个,下一个人算 12%18=12 —— 12
            #     已经有人了,于是重复,而 5 的空缺永远没人补。
            #   · 现在:5 是唯一还空着的格子,下一个人正好补上。
            # 也因此,释放中途退出的被试不再破坏 Williams 平衡。
            rows = conn.execute(
                "SELECT seq, COUNT(*) FROM participants WHERE passed=1 AND seq IS NOT NULL"
                " AND (json_valid(screening_json) = 0"
                "      OR COALESCE(json_extract(screening_json, '$.dev'), 0) != 1)"
                " GROUP BY seq"
            ).fetchall()
            used = {int(r[0]): int(r[1]) for r in rows if r[0] is not None}
            seq = min(range(config.LATIN_SQUARE_N), key=lambda k: (used.get(k, 0), k))
        cur = conn.execute(
            "INSERT INTO participants"
            " (created_at, lang, seq, demographics_json, screening_json, passed, token, status)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (_now(), lang, seq, _dumps(demographics), _dumps(screening),
             int(passed), token, "in_progress" if passed else "screened_out"),
        )
        return int(cur.lastrowid), seq, token


def release_participant(pid: int) -> bool:
    """释放一位被试占用的拉丁方序号:把 screening_json.dev 置 1,数据整行保留。

    为什么是「标记」而不是「删除」:删行会让后续被试的序号算错(见 insert_participant),
    也毁掉已经采到的那部分数据。标记成 dev 之后,这一行同时从三处退出——
    序号分配(insert_participant)、分析纳入(v3.included_participants)、监控面板统计,
    与研究员用 devtools 注入的测试行走同一条排除路径,口径一致。

    ⚠️ 被释放者的 token 仍然有效:他若拿着 ?t= 链接回来,还能把流程走完,但数据不再进分析。
    所以只对**确认不会回来**的会话用(面板里按最后活动时间判断)。
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT screening_json FROM participants WHERE id=?", (pid,)).fetchone()
        if row is None:
            return False
        try:
            d = json.loads(row[0]) if row[0] else {}
        except (ValueError, TypeError):
            d = {}
        if not isinstance(d, dict):
            d = {}
        d["dev"] = True
        d["released_at"] = _now()      # 留痕:事后能分辨「研究员释放」与「devtools 注入」
        conn.execute("UPDATE participants SET screening_json=? WHERE id=?",
                     (_dumps(d), pid))
    return True


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


def get_trial(participant_id: int, round_idx: int) -> Optional[dict]:
    """某被试某轮已提交的 trial 行;没有则 None。续接用它把问卷页原样恢复。"""
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM trials WHERE participant_id=? AND round_idx=?",
                           (participant_id, round_idx)).fetchone()
        return dict(row) if row else None


def count_questionnaires(participant_id: int) -> int:
    """How many rounds this participant has fully completed (a round is done once
    its questionnaire is submitted) — used to resume at the right round."""
    with _conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM questionnaires WHERE participant_id=?",
            (participant_id,),
        ).fetchone()[0]


def get_participant(pid: int) -> Optional[dict]:
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None


def set_contact(pid: int, payload: str) -> bool:
    """写入自愿留下的联系方式;**已有值就拒绝覆盖**,返回是否写成功。

    完成页的 `?t=` 续接 token 会一直留在网址里,`_attempt_resume` 只凭 token 就能恢复
    一位已完成被试的身份。页面从前是只读的,所以转发/共用机器上的残留网址无害;
    这个表单会**写**,所以必须做成一次性的 —— 否则后来者的邮箱会把前一位的悄悄顶掉,
    而记录上写的是前一位同意了被联系。"""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE participants SET contact_json=? WHERE id=? AND contact_json IS NULL",
            (payload, pid),
        )
        return cur.rowcount > 0


def update_participant(pid: int, **fields: Any) -> None:
    _cols(fields)
    cols = ", ".join(f"{k}=?" for k in fields)
    with _conn() as conn:
        conn.execute(
            f"UPDATE participants SET {cols} WHERE id=?", (*fields.values(), pid)
        )


# 去掉 0/O/1/I:被试要把这串码**抄进邮件**,同形字是纯粹的失败源。
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_completion_code(pid: int) -> str:
    """签发完成码并把 status 置 done;**已有码就原样返回**(幂等)。

    两个会话先后到达完成页(第二个 tab 迟一步提交总问卷)时,以前会无条件重签:被试已经
    抄走的那串在库里被顶掉,finished_at 也被推后。finished_at 是 events 表补不了的唯一一段
    (整场时长 = finished_at − created_at),只能签一次。"""
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    with _conn() as conn:
        conn.execute(
            "UPDATE participants SET completion_code=?, status='done', finished_at=?"
            " WHERE id=? AND completion_code IS NULL",
            (code, _now(), pid),
        )
        row = conn.execute("SELECT completion_code FROM participants WHERE id=?", (pid,)).fetchone()
    return row[0] if row and row[0] else code


# ---------------- trials / events / questionnaires ----------------

def insert_trial(**f: Any) -> int:
    f.setdefault("created_at", _now())
    cols = _cols(f.keys())
    marks = ", ".join("?" for _ in f)
    with _conn() as conn:
        # 被顶掉的那次尝试先归档:以前 OR REPLACE 会连 final_output / script_versions / guidance_json
        # 一起物理删除,「重做前后是否一致」的敏感性分析就无从做起。events 只记 v 号,不记正文。
        conn.row_factory = sqlite3.Row
        old = conn.execute("SELECT * FROM trials WHERE participant_id=? AND round_idx=?",
                           (f.get("participant_id"), f.get("round_idx"))).fetchone()
        if old is not None:
            conn.execute("INSERT INTO trials_archive (participant_id, round_idx, archived_at, row_json)"
                         " VALUES (?,?,?,?)", (old["participant_id"], old["round_idx"], _now(),
                                              _dumps(dict(old))))
        conn.row_factory = None
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


def attach_intake_events(participant_id: int, session_id: str) -> None:
    """Backfill the participant id onto this browser session's intake events.

    Consent / how-it-works / screening happen before a participant row exists,
    so those events are written with participant_id NULL at round_idx 0, keyed
    by the browser session id in `attempt`. Screening calls this the moment the
    row is created, which is what makes intake dwell times attributable."""
    if not session_id:
        return
    with _conn() as conn:
        conn.execute(
            "UPDATE events SET participant_id=? WHERE participant_id IS NULL"
            " AND round_idx=0 AND attempt=?",
            (participant_id, session_id),
        )


def insert_questionnaire(**f: Any) -> int:
    f.setdefault("created_at", _now())
    cols = _cols(f.keys())
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

