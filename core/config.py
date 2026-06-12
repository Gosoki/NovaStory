from __future__ import annotations

import os

import streamlit as st

# ---- experiment constants (locked for the whole study; all logged per trial) ----
TEMPERATURE = 0.8
MAX_REGEN = 0            # extra regenerations allowed per generation step (0 = single-shot, no regen confound)
N_DEFAULT_SAMPLES = 3    # pre-sampled "default outlines" fed to ModeMirror
K_GHOST = 20             # offline ghost-run samples per trial (scripts/ only)

CONDITIONS = ("C", "D", "E")
N_ROUNDS = 3
MIN_INTENT_CHARS = 10

# ModeMirror counter-proposal divergence dimensions; one is assigned per participant
# (by seq) so that dissent across participants is pushed in different directions.
DIVERGENCE_POOL = [
    "视角(换一个讲故事的人称或观察角度)",
    "时间结构(打乱时间线:倒叙、循环、闪回等)",
    "基调(反转情绪基调,例如把温情变荒诞、把喜剧变惊悚)",
    "类型混搭(混入另一种类型片元素,如悬疑、科幻、武侠)",
]


def researcher_password() -> str:
    """Researcher mode password: secrets > env > dev default ("nova")."""
    try:
        pw = st.secrets.get("researcher_password", "")
    except Exception:  # secrets file missing entirely
        pw = ""
    return pw or os.environ.get("NOVASTORY_RESEARCHER_PW", "") or "nova"
