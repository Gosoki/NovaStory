from __future__ import annotations

import os
from typing import Optional

import streamlit as st

# ---- experiment constants (locked for the whole study; all logged per trial) ----
TEMPERATURE = 0.8

N_ROUNDS = 3
MIN_INTENT_CHARS = 10

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
    """Researcher mode password: secrets > env > dev default ("nova")."""
    try:
        pw = st.secrets.get("researcher_password", "")
    except Exception:  # secrets file missing entirely
        pw = ""
    return pw or os.environ.get("NOVASTORY_RESEARCHER_PW", "") or "nova"
