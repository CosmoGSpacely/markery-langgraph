"""Integration tests for the LangGraph review graph.

All tool calls (run_card_infer, run_confirm, run_draft, subprocess.run for
generate_card) are mocked — no live Markery CLI or MARKERY_ROOT required.

Fixture: three candidates modelled on radio-pioneers confirmed pairs.
  sterilamp-us2168861a  → recommend "confirm"   (high score)
  minalite-us1829460a   → recommend "reject"    (moderate score)
  victor-us1486221a     → recommend "defer"     (low score)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from langgraph.checkpoint.memory import MemorySaver

# graph.py imports config.MARKERY_ROOT at module level; set a fake value so
# the module can be imported without a real Markery installation.
os.environ.setdefault("MARKERY_ROOT", "/fake/markery")

from langgraph_markery.graph import _slug_for, build_graph
from langgraph_markery.state import ResearchState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CANDIDATES = [
    {
        "patent_no": "US2168861A",
        "trademark": "STERILAMP",
        "trademark_serial": 71423019,
        "entity": "Westinghouse Electric and Manufacturing Company",
        "entity_id": 9,
        "score": 0.799,
    },
    {
        "patent_no": "US1829460A",
        "trademark": "MINALITE",
        "trademark_serial": 71321058,
        "entity": "Westinghouse Electric and Manufacturing Company",
        "entity_id": 9,
        "score": 0.750,
    },
    {
        "patent_no": "US1486221A",
        "trademark": "VICTOR",
        "trademark_serial": 71195203,
        "entity": "Radio Corporation of America",
        "entity_id": 8,
        "score": 0.720,
    },
]

INFER_RESULTS = {
    "sterilamp-us2168861a": {
        "recommendation": "confirm",
        "score": 5,
        "card_text": "# STERILAMP / US2168861A",
        "reasoning": "Directly covers the technology.",
    },
    "minalite-us1829460a": {
        "recommendation": "reject",
        "score": 2,
        "card_text": "# MINALITE / US1829460A",
        "reasoning": "Weak overlap with patent claims.",
    },
    "victor-us1486221a": {
        "recommendation": "defer",
        "score": 3,
        "card_text": "# VICTOR / US1486221A",
        "reasoning": "Needs further research.",
    },
}


def _make_initial(project: str = "radio-pioneers") -> ResearchState:
    return {
        "project": project,
        "queue": [],
        "confirmed_this_session": [],
        "current_slug": None,
        "infer_result": None,
        "session_log": [],
        "recommendation_override": None,
    }


def _mock_card_infer(project, slug, model=None):
    return INFER_RESULTS.get(slug, {"recommendation": "defer", "score": 3,
                                     "card_text": "", "reasoning": ""})


def _mock_candidates_jsonl(tmp_path: Path, project: str) -> Path:
    matches = tmp_path / "projects" / project / "matches"
    matches.mkdir(parents=True)
    with (matches / "candidates.jsonl").open("w") as fh:
        for c in CANDIDATES:
            fh.write(json.dumps(c) + "\n")
    (matches / "confirmed.jsonl").touch()
    (matches / "rejected.jsonl").touch()
    return matches


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGraphRouting:
    """Verify that routing fires correctly across all three recommendation paths."""

    def test_three_candidates_produce_three_infer_results(self, tmp_path):
        matches = _mock_candidates_jsonl(tmp_path, "radio-pioneers")
        graph = build_graph(checkpointer=MemorySaver())
        thread = {"configurable": {"thread_id": "test-routing"}}

        with (
            patch("langgraph_markery.graph.config.MARKERY_ROOT", str(tmp_path)),
            patch("langgraph_markery.graph.run_card_infer", side_effect=_mock_card_infer),
            patch("langgraph_markery.graph.run_confirm"),
            patch("langgraph_markery.graph.run_draft", return_value=("draft ok", True)),
            patch("subprocess.run") as mock_sub,
        ):
            mock_sub.return_value = MagicMock(returncode=0, stdout="", stderr="")

            # Run until first interrupt (human_gate for "sterilamp")
            events = list(graph.stream(_make_initial(), config=thread))
            snapshot = graph.get_state(thread)

            # Graph paused before human_gate
            assert snapshot.next == ("human_gate",), f"Expected human_gate interrupt, got {snapshot.next}"
            assert snapshot.values["current_slug"] == "sterilamp-us2168861a"
            assert snapshot.values["infer_result"]["recommendation"] == "confirm"

    def test_confirm_path_calls_run_confirm(self, tmp_path):
        _mock_candidates_jsonl(tmp_path, "radio-pioneers")
        graph = build_graph(checkpointer=MemorySaver())
        thread = {"configurable": {"thread_id": "test-confirm"}}
        mock_confirm = MagicMock()

        with (
            patch("langgraph_markery.graph.config.MARKERY_ROOT", str(tmp_path)),
            patch("langgraph_markery.graph.run_card_infer", side_effect=_mock_card_infer),
            patch("langgraph_markery.graph.run_confirm", mock_confirm),
            patch("langgraph_markery.graph.run_draft", return_value=("draft ok", True)),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        ):
            # Run to first interrupt
            list(graph.stream(_make_initial(), config=thread))
            snapshot = graph.get_state(thread)
            assert snapshot.next == ("human_gate",)

            # Resume with human "confirm"
            graph.update_state(thread, {"recommendation_override": "confirm"})
            list(graph.stream(None, config=thread))

            mock_confirm.assert_called_once_with(
                "radio-pioneers", "sterilamp-us2168861a", note=pytest.approx("Directly covers the technology.", abs=0)
            )

    def test_reject_path_writes_rejected_jsonl(self, tmp_path):
        matches = _mock_candidates_jsonl(tmp_path, "radio-pioneers")
        graph = build_graph(checkpointer=MemorySaver())
        thread = {"configurable": {"thread_id": "test-reject"}}

        # Only put the MINALITE candidate in the queue so routing goes straight to reject
        single_candidate = [c for c in CANDIDATES if c["trademark"] == "MINALITE"]
        with (matches / "candidates.jsonl").open("w") as fh:
            for c in single_candidate:
                fh.write(json.dumps(c) + "\n")

        with (
            patch("langgraph_markery.graph.config.MARKERY_ROOT", str(tmp_path)),
            patch("langgraph_markery.graph.run_card_infer", side_effect=_mock_card_infer),
            patch("langgraph_markery.graph.run_confirm"),
            patch("langgraph_markery.graph.run_draft", return_value=("draft ok", True)),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        ):
            events = list(graph.stream(_make_initial(), config=thread))

        rejected = json.loads((matches / "rejected.jsonl").read_text().strip())
        assert rejected["patent_no"] == "US1829460A"

    def test_defer_path_appended_to_session_log(self, tmp_path):
        matches = _mock_candidates_jsonl(tmp_path, "radio-pioneers")
        graph = build_graph(checkpointer=MemorySaver())
        thread = {"configurable": {"thread_id": "test-defer"}}

        single_candidate = [c for c in CANDIDATES if c["trademark"] == "VICTOR"]
        with (matches / "candidates.jsonl").open("w") as fh:
            for c in single_candidate:
                fh.write(json.dumps(c) + "\n")

        with (
            patch("langgraph_markery.graph.config.MARKERY_ROOT", str(tmp_path)),
            patch("langgraph_markery.graph.run_card_infer", side_effect=_mock_card_infer),
            patch("langgraph_markery.graph.run_confirm"),
            patch("langgraph_markery.graph.run_draft", return_value=("draft ok", True)),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        ):
            list(graph.stream(_make_initial(), config=thread))

        state = graph.get_state(thread).values
        deferred = [l for l in state["session_log"] if "append_defer" in l]
        assert any("victor-us1486221a" in l for l in deferred)

    def test_empty_queue_terminates_immediately(self, tmp_path):
        matches = tmp_path / "projects" / "radio-pioneers" / "matches"
        matches.mkdir(parents=True)
        (matches / "candidates.jsonl").touch()
        (matches / "confirmed.jsonl").touch()
        (matches / "rejected.jsonl").touch()

        graph = build_graph(checkpointer=MemorySaver())
        thread = {"configurable": {"thread_id": "test-empty"}}

        with patch("langgraph_markery.graph.config.MARKERY_ROOT", str(tmp_path)):
            list(graph.stream(_make_initial(), config=thread))

        state = graph.get_state(thread).values
        assert state["current_slug"] is None
        assert state["confirmed_this_session"] == []

    def test_confirmed_slug_excluded_from_queue(self, tmp_path):
        matches = _mock_candidates_jsonl(tmp_path, "radio-pioneers")
        # Pre-confirm sterilamp so it should be skipped
        with (matches / "confirmed.jsonl").open("w") as fh:
            fh.write(json.dumps({"slug": "sterilamp-us2168861a", "patent_no": "US2168861A"}) + "\n")

        graph = build_graph(checkpointer=MemorySaver())
        thread = {"configurable": {"thread_id": "test-filter-confirmed"}}

        with (
            patch("langgraph_markery.graph.config.MARKERY_ROOT", str(tmp_path)),
            patch("langgraph_markery.graph.run_card_infer", side_effect=_mock_card_infer),
            patch("langgraph_markery.graph.run_confirm"),
            patch("langgraph_markery.graph.run_draft", return_value=("draft ok", True)),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        ):
            list(graph.stream(_make_initial(), config=thread))

        # First slug picked should be minalite (sterilamp excluded), routing → reject
        state = graph.get_state(thread).values
        picked = [l for l in state["session_log"] if "pick_next" in l]
        assert not any("sterilamp" in l for l in picked)

    def test_human_reject_override_does_not_call_run_confirm(self, tmp_path):
        _mock_candidates_jsonl(tmp_path, "radio-pioneers")
        graph = build_graph(checkpointer=MemorySaver())
        thread = {"configurable": {"thread_id": "test-human-reject"}}
        mock_confirm = MagicMock()

        with (
            patch("langgraph_markery.graph.config.MARKERY_ROOT", str(tmp_path)),
            patch("langgraph_markery.graph.run_card_infer", side_effect=_mock_card_infer),
            patch("langgraph_markery.graph.run_confirm", mock_confirm),
            patch("langgraph_markery.graph.run_draft", return_value=("draft ok", True)),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        ):
            list(graph.stream(_make_initial(), config=thread))
            snapshot = graph.get_state(thread)
            assert snapshot.next == ("human_gate",)

            # Human overrides model's "confirm" with "reject"
            graph.update_state(thread, {"recommendation_override": "reject"})
            list(graph.stream(None, config=thread))

        mock_confirm.assert_not_called()


class TestSlugHelper:
    def test_standard_trademark(self):
        assert _slug_for({"trademark": "STERILAMP", "patent_no": "US2168861A"}) == "sterilamp-us2168861a"

    def test_trademark_with_spaces(self):
        assert _slug_for({"trademark": "DE ION", "patent_no": "US1677093A"}) == "de-ion-us1677093a"

    def test_figurative_mark(self):
        assert _slug_for({"trademark": None, "patent_no": "US1234567A"}) == "figurative-us1234567a"

    def test_quoted_trademark(self):
        assert _slug_for({"trademark": '"DE-ION"', "patent_no": "US1677093A"}) == "de-ion-us1677093a"
