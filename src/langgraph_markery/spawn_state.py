"""SpawnState TypedDict for the Phase 32 P4 project-spawning loop."""

from __future__ import annotations

from typing import TypedDict


class SpawnState(TypedDict):
    """State threaded through the spawn graph.

    years:            Filing years to draw design-mark seed pairs from.
    media_sources:    PD-media sources for the preview discovery pass
                      (books are always searched; these add newspapers/images).
    relevance_floor:  Score (1-5) at/above which a discovered book is acquired.
    ledger_path:      JSON file of already-spawned entities (dedup across runs).
    candidates:       Assembled proposals, one per unique entity:
                      {entity, slug, n_pairs, top_score, cpc, is_tech, top_pairs,
                       coverage, tier}. coverage ∈ {ok, thin}; tier ∈ {clean, review}.
    decisions:        Set on resume after the human gate:
                      {entity_key: "approve" | "reject" | "defer"}.
    spawned/rejected/deferred: outcome records per candidate.
    session_log:      Human-readable log lines.
    """

    years: list[int]
    media_sources: list[str]
    relevance_floor: int
    ledger_path: str
    candidates: list[dict]
    decisions: dict[str, str]
    spawned: list[dict]
    rejected: list[str]
    deferred: list[str]
    session_log: list[str]
