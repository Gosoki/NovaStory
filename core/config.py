from __future__ import annotations

import os
from typing import Optional

import streamlit as st

# ---- experiment constants (locked for the whole study; all logged per trial) ----
TEMPERATURE = 0.8

CONDITIONS = ("C", "D", "E")
N_ROUNDS = 3
MIN_INTENT_CHARS = 10

# ---- guided elicitation (condition E) ----
# Round 1 = the three fixed expert dimensions + 2-4 AI-chosen supplements (5-7 total).
GUIDANCE_FIXED_DIMS = ("psychology", "turning_point", "key_shot")
SUPPLEMENT_RANGE = (2, 4)
# Follow-up rounds (user-triggered, unlimited): 1-3 questions each.
FOLLOWUP_RANGE = (1, 3)
# JSON robustness: retries before degrading to one open fallback question.
GUIDANCE_JSON_RETRIES = 2
# Guidance-step model override: index into secrets [[api_configs]], or None to
# follow the session's main config (default = first preset, now KAT-Coder via
# edgefn; mimo retired 2026-06-26). Re-measure guidance latency before formal
# data collection; if the main model is slow (>30s) point this at a faster
# entry (paper/7 D23).
GUIDANCE_API_INDEX: Optional[int] = None


def researcher_password() -> str:
    """Researcher mode password: secrets > env > dev default ("nova")."""
    try:
        pw = st.secrets.get("researcher_password", "")
    except Exception:  # secrets file missing entirely
        pw = ""
    return pw or os.environ.get("NOVASTORY_RESEARCHER_PW", "") or "nova"
