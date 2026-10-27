from __future__ import annotations

from views import interview


def render_pipeline(topic: dict) -> None:
    """Condition F —「想いを聞く」インタビュー then generate (2026-10-27, docs/paper/06 ㉖):
    the AI asks three open questions one at a time right after the intent; the script is
    generated when the last answer is in (views/interview.py), then hand-edit only."""
    interview.begin()
