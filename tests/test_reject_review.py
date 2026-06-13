"""Tests for D065: routing a model `reject` through human_gate.

A confident-looking reject (score >= floor, or any reject under --review-all)
must pause at human_gate so a human can overturn it, instead of being silently
written to rejected.jsonl.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from langgraph.checkpoint.memory import MemorySaver

os.environ.setdefault("MARKERY_ROOT", "/fake/markery")

from langgraph_markery.graph import build_graph, _route_infer


# A reject candidate; score is varied per test.
REJECT_CAND = {
    "patent_no": "US904137A",
    "trademark": None,
    "trademark_serial": 71247861,
    "entity": "Mack Trucks",
    "entity_id": 30,
    "score": 0.6,
}


def _initial(review_all=False, floor=3):
    return {
        "project": "animal-marks-1930",
        "queue": [],
        "confirmed_this_session": [],
        "current_slug": None,
        "infer_result": None,
        "session_log": [],
        "recommendation_override": None,
        "review_all": review_all,
        "reject_review_floor": floor,
    }


def _seed(tmp_path, score):
    matches = tmp_path / "projects" / "animal-marks-1930" / "matches"
    matches.mkdir(parents=True)
    (matches / "candidates.jsonl").write_text(json.dumps(REJECT_CAND) + "\n")
    (matches / "confirmed.jsonl").touch()
    (matches / "rejected.jsonl").touch()
    return matches


def _infer(score):
    def _f(project, slug, model=None):
        return {"recommendation": "reject", "score": score,
                "card_text": "# card", "reasoning": "founder, not the company"}
    return _f


# ── unit: _route_infer ─────────────────────────────────────────────────────

class TestRouteInfer:
    def _state(self, rec, score, review_all=False, floor=3):
        return {"infer_result": {"recommendation": rec, "score": score},
                "review_all": review_all, "reject_review_floor": floor}

    def test_low_score_reject_auto_rejects(self):
        assert _route_infer(self._state("reject", 2)) == "write_rejected"

    def test_high_score_reject_goes_to_gate(self):
        assert _route_infer(self._state("reject", 4)) == "human_gate"

    def test_score_at_floor_goes_to_gate(self):
        assert _route_infer(self._state("reject", 3, floor=3)) == "human_gate"

    def test_review_all_surfaces_low_reject(self):
        assert _route_infer(self._state("reject", 1, review_all=True)) == "human_gate"

    def test_review_all_surfaces_defer(self):
        assert _route_infer(self._state("defer", 2, review_all=True)) == "human_gate"

    def test_defer_without_review_all_appends(self):
        assert _route_infer(self._state("defer", 2)) == "append_defer"

    def test_confirm_always_gate(self):
        assert _route_infer(self._state("confirm", 1)) == "human_gate"


# ── integration: graph pauses and can overturn ─────────────────────────────

class TestRejectReviewGraph:
    def test_high_score_reject_pauses_at_gate(self, tmp_path):
        _seed(tmp_path, score=4)
        graph = build_graph(checkpointer=MemorySaver())
        thread = {"configurable": {"thread_id": "t-reject-gate"}}
        with (
            patch("langgraph_markery.graph.config.MARKERY_ROOT", str(tmp_path)),
            patch("langgraph_markery.graph.run_card_infer", side_effect=_infer(4)),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        ):
            list(graph.stream(_initial(floor=3), config=thread))
            snap = graph.get_state(thread)
            assert snap.next == ("human_gate",)
            assert snap.values["infer_result"]["recommendation"] == "reject"

    def test_low_score_reject_no_gate_writes_file(self, tmp_path):
        matches = _seed(tmp_path, score=2)
        graph = build_graph(checkpointer=MemorySaver())
        thread = {"configurable": {"thread_id": "t-reject-auto"}}
        with (
            patch("langgraph_markery.graph.config.MARKERY_ROOT", str(tmp_path)),
            patch("langgraph_markery.graph.run_card_infer", side_effect=_infer(2)),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        ):
            list(graph.stream(_initial(floor=3), config=thread))
            snap = graph.get_state(thread)
            assert snap.next == ()  # ran to completion, no interrupt
        rejected = json.loads((matches / "rejected.jsonl").read_text().strip())
        assert rejected["patent_no"] == "US904137A"

    def test_human_overturns_reject_to_confirm(self, tmp_path):
        _seed(tmp_path, score=1)
        graph = build_graph(checkpointer=MemorySaver())
        thread = {"configurable": {"thread_id": "t-overturn"}}
        mock_confirm = MagicMock()
        with (
            patch("langgraph_markery.graph.config.MARKERY_ROOT", str(tmp_path)),
            patch("langgraph_markery.graph.run_card_infer", side_effect=_infer(1)),
            patch("langgraph_markery.graph.run_confirm", mock_confirm),
            patch("langgraph_markery.graph.run_draft", return_value=("ok", True)),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        ):
            # --review-all surfaces even the score-1 reject (the bulldog case)
            list(graph.stream(_initial(review_all=True), config=thread))
            assert graph.get_state(thread).next == ("human_gate",)
            graph.update_state(thread, {"recommendation_override": "confirm"})
            list(graph.stream(None, config=thread))
        mock_confirm.assert_called_once()
