from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent


def _git_rev() -> str:
    """当前代码版本(git HEAD 短 sha,不起子进程)。写进每一行 trial:采数期间改代码是被预期的
    (冻结允许偏差记录),没有这一列就说不清哪些行是哪个版本产生的。"""
    try:
        git = _ROOT / ".git"
        if git.is_file():   # worktree:.git 是一个指向 gitdir 的文件
            git = Path(git.read_text().split(":", 1)[1].strip())
        head = (git / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = git / head[5:]
            if not ref.exists():          # 共享仓库的 worktree:refs 在主 gitdir 下
                ref = git.parent.parent / head[5:] if git.name != ".git" else ref
            return ref.read_text().strip()[:12] if ref.exists() else head[5:][-12:]
        return head[:12]
    except OSError:
        return ""


APP_REV = _git_rev()

# ---- experiment constants (locked for the whole study; all logged per trial) ----
TEMPERATURE = 0.8

N_ROUNDS = 3
# Floor on the intent statement. 8 rather than 10 so that a bare tone-only idea
# ("明るい話にしたい") clears it — the intro page tells participants any length is
# fine, and a floor that contradicts that copy would block the very inputs we
# invite (B6, 2026-08-03). Still non-zero: an empty/one-word intent gives the
# fidelity baseline nothing to anchor on.
MIN_INTENT_CHARS = 8

# Number of Latin-square sequences = len(state._COND_ORDERS) × len(state._TOPIC_ORDERS).
# 6 condition orders (all permutations of C/D/E → first-order carryover balanced,
# a Williams design) × 3 topic rotations = 18. N=36 → 2 completers per seq.
# state.py asserts this stays in sync. (deep-review 2026-07-19 #1)
LATIN_SQUARE_N = 18

# ---- 日本队列:4 条件设计(2026-10-27,docs/paper/06 ㉖)----
# ja 被试做 C / D / E / F 四个条件;zh / en 保持上面的 3 条件 18 序列 —— 中国队列是已采完的
# 数据,它的轮数、序列表、序列计数一个都不能变。F =「想いを聞く」インタビュー(views/interview.py)。
# 4 条件 Williams 方阵 4 个序列 × 4 个题目轮换 = 16(state._COND_ORDERS_F / _TOPIC_ORDERS_F)。
# ⚠️ 轮数写死在各语言的文案里(ja「4 つのお題 / 全 4 ラウンド / 約15〜20分」,zh / en 是 3):
#    改这个集合时要同步改 i18n/locales 的 consent.body、intro.flow、final_survey.hint / q_noticed。
FOUR_COND_LANGS = frozenset({"ja"})
N_ROUNDS_F = 4
LATIN_SQUARE_N_F = 16
INTERVIEW_N = 3   # F 的采访题数(第 1 题固定,第 2、3 题由 AI 按上一个回答调整措辞)


def rounds_for_lang(lang: str) -> int:
    """这一语言的被试做几轮(ja 4、其余 3)。"""
    return N_ROUNDS_F if lang in FOUR_COND_LANGS else N_ROUNDS


def seq_count_for_lang(lang: str) -> int:
    """这一语言的序列空间大小(ja 16、其余 18)。两个空间分开计数,见 db.insert_participant。"""
    return LATIN_SQUARE_N_F if lang in FOUR_COND_LANGS else LATIN_SQUARE_N

# ---- guided elicitation (condition E) ----
# Round 1 = 3 fixed expert dimensions + 2-4 AI-chosen supplements (5-7 total);
# follow-up rounds 1-3 questions each. Both enforced in the prompts
# (core/prompts.py), not re-validated in code.
SUPPLEMENT_RANGE = (2, 4)
FOLLOWUP_RANGE = (1, 3)
# JSON robustness: retries before degrading to one open fallback question.
GUIDANCE_JSON_RETRIES = 2
# Guidance-step model override: index into secrets [[api_configs]], or None to
# follow the session's main config (paper/7 D23). Formal study decision (B9,
# 2026-06-26): everything runs on OpenAI, guidance step included — keep None
# and point the first secrets preset at OpenAI; edgefn presets are dev-only.
GUIDANCE_API_INDEX: Optional[int] = None


def researcher_password() -> str:
    """Researcher mode password: secrets > env. **Empty = locked**(fail-closed).

    以前兜底到字面量 "nova":一次配置疏漏就等于公网上一个已知口令的后台。
    本地开发把 researcher_password 写进 .streamlit/secrets.toml(见 secrets.toml.example)。"""
    try:
        pw = st.secrets.get("researcher_password", "")
    except Exception:  # secrets file missing entirely
        pw = ""
    return pw or os.environ.get("NOVASTORY_RESEARCHER_PW", "")
