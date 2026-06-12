from __future__ import annotations

from views import guidance


def render_pipeline(topic: dict) -> None:
    """Condition E — guide-then-generate: round-1 elicitation starts immediately
    after the intent; script generation happens when the answers are submitted
    (see views/guidance.py), then the post-generation loop takes over."""
    guidance.begin_round("fixed3+ai_supplement")
