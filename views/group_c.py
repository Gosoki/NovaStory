from __future__ import annotations

from views._first_gen import render_first_generation


def render_pipeline(topic: dict) -> None:
    """Condition C — one-shot: intent → full script, view, submit (no loop)."""
    render_first_generation(topic, "C-final")
