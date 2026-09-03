from __future__ import annotations

from views._first_gen import render_first_generation


def render_pipeline(topic: dict) -> None:
    """Condition D — generate-then-repair: same first generation as C, then the
    post-generation loop (free-form revision requests + direct editing)."""
    render_first_generation(topic, "D-final")
