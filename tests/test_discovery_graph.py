"""Tests for the Phase 30 discovery loop graph. All Markery CLI tools are mocked.

A single tick over three candidates exercises the whole boundary:
  Acquire High (free, relevant)  → acquire_free
  ILL High     (book, relevant)  → human_gate → (resume queue) → queue_ill
  Acquire Low  (free, irrelevant)→ log_dropped
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from langgraph.checkpoint.memory import MemorySaver

os.environ.setdefault("MARKERY_ROOT", "/fake/markery")

from langgraph_markery.discovery_graph import build_graph, initial_state

_CANDIDATES = [
    {"title": "Acquire High", "author": "A", "year": 1925, "isbn": "1",
     "action": "acquire", "ia_id": "ah00", "worldcat_url": "w1", "ill_request": ""},
    {"title": "ILL High", "author": "B", "year": 1926, "isbn": "2",
     "action": "ill", "ia_id": None, "worldcat_url": "w2", "ill_request": "ILL REQUEST ..."},
    {"title": "Acquire Low", "author": "C", "year": 1927, "isbn": "3",
     "action": "acquire", "ia_id": "al00", "worldcat_url": "w3", "ill_request": ""},
]

_SCORES = {"Acquire High": 5, "ILL High": 5, "Acquire Low": 1}


def _patches(**extra):
    base = {
        "run_books": MagicMock(return_value=list(_CANDIDATES)),
        "run_relevance": MagicMock(side_effect=lambda proj, title, text="": {
            "score": _SCORES.get(title, 0), "reasoning": "r"}),
        "run_acquire_text": MagicMock(return_value=True),
        "run_wants_add": MagicMock(),
        "run_leads_add": MagicMock(),
        # Media tools default to no-ops so book-focused tests stay hermetic.
        "run_media_search": MagicMock(return_value=[]),
        "run_media_acquire": MagicMock(return_value={"acquired": False}),
        "run_use": MagicMock(return_value=True),
    }
    base.update(extra)
    return base


def test_full_tick_acquire_gate_drop(tmp_path):
    mocks = _patches()
    graph = build_graph(MemorySaver())
    thread = {"configurable": {"thread_id": "disc-1"}}
    with (
        patch("langgraph_markery.discovery_graph.config.resolve_markery_root",
              return_value=str(tmp_path)),
        patch.multiple("langgraph_markery.discovery_graph.tools", **mocks),
    ):
        # Run until the ILL human_gate interrupt.
        list(graph.stream(initial_state("tools", relevance_floor=3), config=thread))
        snap = graph.get_state(thread)
        assert snap.next == ("human_gate",), f"expected human_gate, got {snap.next}"
        # Free acquisition already happened before the gate.
        assert mocks["run_acquire_text"].call_count == 1

        # Human approves the ILL want.
        graph.update_state(thread, {"decision_override": "queue"})
        list(graph.stream(None, config=thread))

    final = graph.get_state(thread).values
    assert final["acquired"] == 1          # Acquire High
    assert final["queued"] == 1            # ILL High (human-approved)
    assert final["logged"] == 3            # all three logged as leads
    mocks["run_wants_add"].assert_called_once()
    # Acquire Low was dropped (below floor), not acquired.
    assert mocks["run_acquire_text"].call_count == 1


def test_media_branch_acquires_and_uses(tmp_path):
    """A PD/fair-use media hit is auto-acquired and referenced into the project."""
    mocks = _patches(
        run_books=MagicMock(return_value=[]),   # media-only tick
        run_relevance=MagicMock(return_value={"score": 5, "reasoning": "on-topic"}),
        run_media_search=MagicMock(side_effect=lambda q, source, max_results=10: (
            [{"source": source, "id": f"{source}-1"}] if source == "commons" else [])),
        run_media_acquire=MagicMock(return_value={
            "acquired": True, "slug": "commons-logo", "license": "InC", "source": "commons"}),
    )
    graph = build_graph(MemorySaver())
    thread = {"configurable": {"thread_id": "disc-media"}}
    with (
        patch("langgraph_markery.discovery_graph.config.resolve_markery_root",
              return_value=str(tmp_path)),
        patch.multiple("langgraph_markery.discovery_graph.tools", **mocks),
    ):
        list(graph.stream(
            initial_state("tools", relevance_floor=3, media_sources=["commons"]),
            config=thread))

    final = graph.get_state(thread).values
    assert final["acquired"] == 1                       # the media item acquired
    mocks["run_media_acquire"].assert_called_once_with("commons", "commons-1", fair_use=True)
    mocks["run_use"].assert_called_once_with("commons-logo", "tools")
    # logged as a lead under its source
    assert mocks["run_leads_add"].call_args.args[0] == "commons"


def test_model_outage_logs_unscored_not_dropped(tmp_path):
    """D077: when the whole free model chain is down (relevance score None), items
    are logged 'unscored' (re-scored on a later tick), never terminally 'dropped'."""
    mocks = _patches(run_relevance=MagicMock(return_value={"score": None, "error": "429"}))
    graph = build_graph(MemorySaver())
    thread = {"configurable": {"thread_id": "disc-outage"}}
    with (
        patch("langgraph_markery.discovery_graph.config.resolve_markery_root",
              return_value=str(tmp_path)),
        patch.multiple("langgraph_markery.discovery_graph.tools", **mocks),
    ):
        list(graph.stream(initial_state("tools", relevance_floor=3), config=thread))
    final = graph.get_state(thread).values
    assert final["acquired"] == 0                          # nothing acquired blind
    statuses = [c.kwargs.get("status") for c in mocks["run_leads_add"].call_args_list]
    assert statuses and all(s == "unscored" for s in statuses)   # not 'dropped'


def test_ill_skip_drops_instead_of_queue(tmp_path):
    mocks = _patches()
    graph = build_graph(MemorySaver())
    thread = {"configurable": {"thread_id": "disc-2"}}
    with (
        patch("langgraph_markery.discovery_graph.config.resolve_markery_root",
              return_value=str(tmp_path)),
        patch.multiple("langgraph_markery.discovery_graph.tools", **mocks),
    ):
        list(graph.stream(initial_state("tools", relevance_floor=3), config=thread))
        graph.update_state(thread, {"decision_override": "skip"})
        list(graph.stream(None, config=thread))

    final = graph.get_state(thread).values
    assert final["queued"] == 0
    mocks["run_wants_add"].assert_not_called()
