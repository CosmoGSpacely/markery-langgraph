"""Tests for run_card_infer's v1.1 JSON contract parsing (and legacy fallback).

No live Markery CLI — subprocess.run is mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from langgraph_markery.tools import run_card_infer


def _mock_run(stdout: str):
    return MagicMock(returncode=0, stdout=stdout, stderr="")


def test_parses_json_contract():
    payload = json.dumps({
        "recommendation": "reject", "score": 1,
        "reasoning": "Assignee is an individual, not the corporation.",
        "card_text": "## CARD: figurative-us904137a  [candidate  0.34  gap=18.4y]",
    })
    with patch("subprocess.run", return_value=_mock_run(payload)):
        result = run_card_infer("animal-marks-1930", "figurative-us904137a")
    assert result["recommendation"] == "reject"
    assert result["score"] == 1
    assert "individual" in result["reasoning"]
    assert result["card_text"].startswith("## CARD:")


def test_json_recommendation_lowercased():
    payload = json.dumps({"recommendation": "CONFIRM", "score": 4,
                          "reasoning": "r", "card_text": "c"})
    with patch("subprocess.run", return_value=_mock_run(payload)):
        assert run_card_infer("p", "s")["recommendation"] == "confirm"


def test_falls_back_to_legacy_infer_block():
    # Older Markery without --json: stdout is the card + human [infer] block.
    legacy = (
        "## CARD: soundex-us1261167a  [confirmed  0.57  gap=9.0y]\n"
        "mark: SOUNDEX\n"
        "\n[infer]  recommendation=confirm  score=4\n"
        "         Strong temporal and entity alignment."
    )
    with patch("subprocess.run", return_value=_mock_run(legacy)):
        result = run_card_infer("information-systems", "soundex-us1261167a")
    assert result["recommendation"] == "confirm"
    assert result["score"] == 4
    assert "alignment" in result["reasoning"]


def test_falls_back_to_defer_on_garbage():
    with patch("subprocess.run", return_value=_mock_run("not json, no infer block")):
        result = run_card_infer("p", "s")
    assert result["recommendation"] == "defer"
    assert result["score"] == 3
