from __future__ import annotations

from views import guidance


def render_pipeline(topic: dict) -> None:
    """Condition F (2026-11-03, docs/paper/06 ㉜) — the same guide-then-generate flow as E
    (views/guidance.py), except the AI's options start hidden: each question first asks
    「選択肢を表示しますか？」, revealing them takes config.F_REVEAL_DELAY_S, and there is
    no "leave it to the AI". Replaces the 10-27「想いを聞く」interview (06 ㉖)."""
    guidance.begin_round("fixed3+ai_supplement")
