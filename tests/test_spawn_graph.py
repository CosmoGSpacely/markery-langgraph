"""Tests for the Phase 32 P4 spawn loop. All Markery CLI tools are mocked."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from langgraph.checkpoint.memory import MemorySaver

os.environ.setdefault("MARKERY_ROOT", "/fake/markery")

from langgraph_markery.spawn_graph import build_graph, initial_state


def _pair(applicant, mark, pno, score, cpc, tech=True):
    return {"serial": "1", "mark": mark, "applicant": applicant, "is_tech": tech,
            "match": "exact", "owner_conf": 1.0, "assignee": applicant,
            "patent_no": pno, "app_dt": "1920-01-01", "grant_dt": "1921-01-01",
            "cpc": cpc, "delta_years": 0.0, "score": score, "year": 1921}


# A "clean" entity (many pairs, high score, >1 CPC) and a "thin" one (1 pair).
_PAIRS = [
    _pair("Acme Co.", "RECTIGON", "US1A", 1.00, ["H02M"]),
    _pair("Acme Co.", "RECTIGON", "US2A", 0.95, ["H02J"]),
    _pair("Acme Co.", "DE-ION", "US3A", 0.90, ["H01H"]),
    _pair("Tiny LLC", "WIDGET", "US9A", 0.50, ["B42F"]),
]


def _patches(**extra):
    base = {
        "run_seed_pairs": MagicMock(return_value=list(_PAIRS)),
        "run_project_init": MagicMock(return_value=True),
        "run_seed_project": MagicMock(return_value={"entity_id": 9001}),
        "run_build_entities": MagicMock(return_value=True),
        "run_match": MagicMock(return_value=True),
        "run_books": MagicMock(return_value=[]),
        "run_relevance": MagicMock(return_value={"score": 5, "reasoning": "r"}),
        "run_media_search": MagicMock(return_value=[]),
        "run_media_acquire": MagicMock(return_value={"acquired": False}),
        "run_use": MagicMock(return_value=True),
        "run_acquire_text": MagicMock(return_value=True),
        "run_wants_add": MagicMock(),
        "run_leads_add": MagicMock(),
        "run_confirm": MagicMock(return_value=None),
        "run_draft": MagicMock(return_value=("drafted", True)),
        "run_site_build": MagicMock(return_value=True),
    }
    base.update(extra)
    return base


def _ledger(tmp_path):
    return str(tmp_path / "spawn_ledger.json")


def test_assemble_tiers_and_sort(tmp_path):
    graph = build_graph(MemorySaver())
    thread = {"configurable": {"thread_id": "s1"}}
    with patch.multiple("langgraph_markery.spawn_graph.tools", **_patches()):
        list(graph.stream(initial_state([1921], ledger_path=_ledger(tmp_path)), config=thread))
        snap = graph.get_state(thread)
    assert snap.next == ("human_gate",)                      # paused at the single gate
    cands = {c["entity"]: c for c in snap.values["candidates"]}
    assert cands["Acme Co."]["tier"] == "clean" and cands["Acme Co."]["coverage"] == "ok"
    assert cands["Tiny LLC"]["tier"] == "review" and cands["Tiny LLC"]["coverage"] == "thin"
    # clean candidates sort first
    assert snap.values["candidates"][0]["tier"] == "clean"


def test_batch_gate_spawns_only_approved(tmp_path):
    mocks = _patches()
    graph = build_graph(MemorySaver())
    thread = {"configurable": {"thread_id": "s2"}}
    with patch.multiple("langgraph_markery.spawn_graph.tools", **mocks):
        list(graph.stream(initial_state([1921], ledger_path=_ledger(tmp_path)), config=thread))
        # Approve Acme, reject Tiny — one batch decision.
        graph.update_state(thread, {"decisions": {"ACME CO.": "approve", "TINY LLC": "reject"}})
        list(graph.stream(None, config=thread))
    final = graph.get_state(thread).values
    assert [s["slug"] for s in final["spawned"]] == ["acme-co"]
    assert final["rejected"] == ["TINY LLC"]
    # the approved one ran the full chain, terminating at a LOCAL site build
    mocks["run_project_init"].assert_called_once_with("acme-co")
    mocks["run_match"].assert_called_once_with("acme-co")
    mocks["run_site_build"].assert_called_once_with("acme-co")
    mocks["run_draft"].assert_called_once()                  # essay auto-drafted


def test_dedup_ledger_skips_spawned(tmp_path):
    import json
    lp = tmp_path / "spawn_ledger.json"
    lp.write_text(json.dumps({"entities": {"ACME CO.": {"slug": "acme-co"}}}))
    graph = build_graph(MemorySaver())
    thread = {"configurable": {"thread_id": "s3"}}
    with patch.multiple("langgraph_markery.spawn_graph.tools", **_patches()):
        list(graph.stream(initial_state([1921], ledger_path=str(lp)), config=thread))
        snap = graph.get_state(thread)
    keys = {c["key"] for c in snap.values["candidates"]}
    assert "ACME CO." not in keys and "TINY LLC" in keys     # already-spawned skipped


def test_discovery_all_sources_and_ledger_record(tmp_path):
    mocks = _patches(
        run_books=MagicMock(return_value=[
            {"title": "Acme History", "author": "A", "year": 1925, "isbn": "1",
             "action": "acquire", "ia_id": "acme01"},
            {"title": "Rare Vol", "author": "B", "year": 1926, "isbn": "2",
             "action": "ill", "ia_id": None, "ill_request": "ILL ..."}]),
        run_media_search=MagicMock(side_effect=lambda q, source, max_results=5: (
            [{"source": source, "id": f"{source}-1"}] if source in ("commons", "chronam") else [])),
        run_media_acquire=MagicMock(return_value={"acquired": True, "slug": "img-1", "license": "PD"}),
    )
    graph = build_graph(MemorySaver())
    thread = {"configurable": {"thread_id": "s4"}}
    lp = _ledger(tmp_path)
    with patch.multiple("langgraph_markery.spawn_graph.tools", **mocks):
        list(graph.stream(initial_state([1921], ledger_path=lp), config=thread))
        graph.update_state(thread, {"decisions": {"ACME CO.": "approve"}})
        list(graph.stream(None, config=thread))
    final = graph.get_state(thread).values
    rec = final["spawned"][0]
    assert rec["books"] == 1 and rec["ill_queued"] == 1     # free acquired, ILL queued not gated
    assert rec["media"] == 2                                 # commons + chronam (newspapers/images)
    mocks["run_use"].assert_called()                         # media referenced into the project
    # newspapers source (chronam) was searched
    assert any(call.kwargs.get("source") == "chronam" or "chronam" in call.args
               for call in mocks["run_media_search"].call_args_list)
    # ledger persisted
    import json
    assert "ACME CO." in json.loads(open(lp).read())["entities"]
