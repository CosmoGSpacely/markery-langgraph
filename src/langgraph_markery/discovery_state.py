"""DiscoveryState TypedDict for the continuous discovery loop (Phase 30)."""

from __future__ import annotations

from typing import TypedDict


class DiscoveryState(TypedDict):
    """State threaded through the discovery graph.

    project:            Markery project discovery runs for.
    seeds:              Search queries (derived from the project's entities).
    candidates:         Discovered candidates, each tagged with `type`:
                        - book  (from `librarian books --json`): {title, author,
                          year, isbn, action, ia_id, worldcat_url, ill_request}.
                        - media (from `librarian media-search --json`): {source,
                          id, title, query, action="acquire"} — PD/fair-use images.
    media_sources:      PD-media sources to search per seed (commons/loc/nara/ia/...).
    current:            Candidate under consideration this cycle (with `score`).
    relevance_floor:    Score (1-5) at/above which a candidate is acted on.
    decision_override:  Set on graph resume after human_gate: "queue" | "skip".
    session_log:        Human-readable log lines per node.
    acquired/queued/logged: per-tick counters (free acquires, ILL wants queued,
                        leads logged).
    """

    project: str
    seeds: list[str]
    media_sources: list[str]
    candidates: list[dict]
    current: dict | None
    relevance_floor: int
    decision_override: str | None
    session_log: list[str]
    acquired: int
    queued: int
    logged: int
