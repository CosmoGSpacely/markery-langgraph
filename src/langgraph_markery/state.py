"""ResearchState TypedDict for the LangGraph review workflow."""

from __future__ import annotations

from typing import TypedDict


class ResearchState(TypedDict):
    """Mutable state threaded through the LangGraph review graph.

    project:                 Markery project name (directory under projects/).
    queue:                   Unreviewed candidate dicts from the project's
                             candidate pool (loaded by load_digest node).
    confirmed_this_session:  Slugs confirmed during this graph run.
    current_slug:            Slug being processed by the current node cycle.
    infer_result:            Parsed output from the last card --infer call:
                             {"recommendation", "score", "reasoning", "card_text"}.
    session_log:             Human-readable log lines appended by each node.
    recommendation_override: When set by the caller on graph resume, human_gate
                             uses this value instead of interrupting again.
                             Expected: "confirm" or "reject". Cleared after use.
    """

    project: str
    queue: list[dict]
    confirmed_this_session: list[str]
    current_slug: str | None
    infer_result: dict | None
    session_log: list[str]
    recommendation_override: str | None
