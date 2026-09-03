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
