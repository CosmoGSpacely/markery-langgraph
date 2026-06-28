"""Continuous historian discovery loop (Phase 30 P5).

A LangGraph workflow that grows the Markery library with little supervision:

    load_seed → discover → pick_next → score → route
                                                 ├─ relevant + free   → acquire_free
                                                 ├─ relevant + ILL    → human_gate → queue_ill / drop
                                                 └─ irrelevant        → log_dropped

The **auto-acquire-free / gate-everything-else** boundary (the user's decision):
free public-domain full text is acquired automatically; anything needing an ILL
(cost/commitment) interrupts for a human. Every candidate is logged as a lead
either way (the discovery log = the loop's memory + the human's audit trail).

Shells the Markery CLI only (contract v1.2); never imports markery. Runs as a
**persistent service the user toggles** — see ``runner.py`` / ``markery historian
discovery {on|off|status}``. One ``build_graph()`` run is one discovery tick.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from langgraph_markery import config, tools
from langgraph_markery.discovery_state import DiscoveryState

_MAX_CANDIDATES = 25
# Media discovery (permissive non-commercial fair-use policy): keyless, reliable
# PD-media sources searched per seed; bounded so a tick stays cheap.
_DEFAULT_MEDIA_SOURCES = ["commons", "loc", "nara", "ia"]
_MEDIA_PER_SOURCE = 5
_MAX_MEDIA = 25


def _log(state: DiscoveryState, msg: str) -> None:
    state["session_log"].append(msg)


def load_seed(state: DiscoveryState) -> DiscoveryState:
    """Derive seed queries from the project's entities.csv (canonical names)."""
    root = config.resolve_markery_root() or config.MARKERY_ROOT
    seeds: list[str] = []
    ents = Path(root) / "projects" / state["project"] / "entities.csv"
    if ents.exists():
        with ents.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("canonical_name"):
                    seeds.append(row["canonical_name"])
    if not seeds:
        seeds = [state["project"].replace("-", " ")]
    state["seeds"] = seeds
    _log(state, f"seeds: {', '.join(seeds)}")
    return state


def discover(state: DiscoveryState) -> DiscoveryState:
    """Discover books (Open Library) and PD/fair-use media (media-search) per seed.

    Books may be free (IA) or ILL-gated; media are auto-acquired under the
    permissive non-commercial fair-use policy. Each candidate is tagged `type`."""
    seen: set[str] = set()
    candidates: list[dict] = []
    for seed in state["seeds"]:
        for c in tools.run_books(seed, max_results=10):
            key = (c.get("title") or "").lower().strip()
            if key and key not in seen:
                seen.add(key)
                c["type"] = "book"
                candidates.append(c)
            if len(candidates) >= _MAX_CANDIDATES:
                break

    media_seen: set[tuple] = set()
    media: list[dict] = []
    for seed in state["seeds"]:
        for src in state.get("media_sources", _DEFAULT_MEDIA_SOURCES):
            for hit in tools.run_media_search(seed, source=src, max_results=_MEDIA_PER_SOURCE):
                key = (hit["source"], hit["id"])
                if key in media_seen:
                    continue
                media_seen.add(key)
                media.append({"type": "media", "source": hit["source"], "id": hit["id"],
                              "title": seed, "query": seed, "action": "acquire"})
                if len(media) >= _MAX_MEDIA:
                    break
    candidates += media
    state["candidates"] = candidates
    _log(state, f"discovered {len(candidates) - len(media)} book(s) + {len(media)} media")
    return state


def pick_next(state: DiscoveryState) -> DiscoveryState:
    state["current"] = state["candidates"].pop(0) if state["candidates"] else None
    return state


def score(state: DiscoveryState) -> DiscoveryState:
    cur = state["current"]
    result = tools.run_relevance(state["project"], cur.get("title", ""))
    cur["score"] = int(result.get("score", 0))
    cur["reasoning"] = result.get("reasoning", "")
    _log(state, f"score {cur['score']}/5 — {cur.get('title','')[:50]}")
    return state


def _lead_id(cur: dict) -> str:
    return (cur.get("title") or "untitled").lower().replace(" ", "-")[:60]


def acquire_free(state: DiscoveryState) -> DiscoveryState:
    cur = state["current"]
    if cur.get("type") == "media":
        res = tools.run_media_acquire(cur["source"], cur["id"], fair_use=True)
        ok = bool(res.get("acquired"))
        if ok and res.get("slug"):
            tools.run_use(res["slug"], state["project"])   # → references/library.jsonl
        tools.run_leads_add(cur["source"], cur["id"], title=cur.get("title", ""),
                            project=state["project"], relevance=cur.get("score"),
                            status="acquired" if ok else "logged",
                            note=f"media {res.get('license', '')}".strip())
        state["acquired"] += int(ok)
        state["logged"] += 1
        _log(state, f"{'acquired' if ok else 'acquire-failed'} media: "
                    f"{cur['source']}/{cur['id']} [{res.get('license', '')}]")
        return state
    ok = tools.run_acquire_text(cur["ia_id"])
    status = "acquired" if ok else "logged"
    tools.run_leads_add("openlibrary", _lead_id(cur), title=cur.get("title", ""),
                        project=state["project"], relevance=cur.get("score"),
                        status=status, note=f"IA {cur['ia_id']}")
    state["acquired"] += int(ok)
    state["logged"] += 1
    _log(state, f"{'acquired' if ok else 'acquire-failed'}: {cur.get('title','')[:50]}")
    return state


def human_gate(state: DiscoveryState) -> DiscoveryState:
    cur = state["current"]
    override = state.get("decision_override")
    if override in ("queue", "skip"):
        decision = override
    else:
        decision = interrupt({
            "action": "ill",
            "title": cur.get("title", ""),
            "score": cur.get("score"),
            "worldcat_url": cur.get("worldcat_url", ""),
            "ill_request": cur.get("ill_request", ""),
            "prompt": "Queue this ILL want? (queue/skip)",
        })
        if not isinstance(decision, str) or decision not in ("queue", "skip"):
            decision = "skip"
    state["decision_override"] = decision
    return state


def queue_ill(state: DiscoveryState) -> DiscoveryState:
    cur = state["current"]
    tools.run_wants_add(cur.get("title", ""), author=cur.get("author", ""),
                        year=cur.get("year"), isbn=cur.get("isbn"),
                        ill_request=cur.get("ill_request", ""))
    tools.run_leads_add("openlibrary", _lead_id(cur), title=cur.get("title", ""),
                        project=state["project"], relevance=cur.get("score"),
                        status="queued", note="ILL want queued (human-gated)")
    state["decision_override"] = None
    state["queued"] += 1
    state["logged"] += 1
    _log(state, f"queued ILL: {cur.get('title','')[:50]}")
    return state


def log_dropped(state: DiscoveryState) -> DiscoveryState:
    cur = state["current"]
    tools.run_leads_add("openlibrary", _lead_id(cur), title=cur.get("title", ""),
                        project=state["project"], relevance=cur.get("score"),
                        status="dropped", note=cur.get("reasoning", "")[:120])
    state["decision_override"] = None
    state["logged"] += 1
    _log(state, f"dropped: {cur.get('title','')[:50]}")
    return state


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _route_pick(state: DiscoveryState) -> Literal["score", "__end__"]:
    return "score" if state["current"] is not None else "__end__"


def _route_after_score(state: DiscoveryState) -> Literal["acquire_free", "human_gate", "log_dropped"]:
    cur = state["current"]
    if cur.get("score", 0) < state["relevance_floor"]:
        return "log_dropped"
    return "acquire_free" if cur.get("action") == "acquire" else "human_gate"


def _route_human(state: DiscoveryState) -> Literal["queue_ill", "log_dropped"]:
    return "queue_ill" if state.get("decision_override") == "queue" else "log_dropped"


def build_graph(checkpointer=None):
    builder = StateGraph(DiscoveryState)
    builder.add_node("load_seed", load_seed)
    builder.add_node("discover", discover)
    builder.add_node("pick_next", pick_next)
    builder.add_node("score", score)
    builder.add_node("acquire_free", acquire_free)
    builder.add_node("human_gate", human_gate)
    builder.add_node("queue_ill", queue_ill)
    builder.add_node("log_dropped", log_dropped)

    builder.set_entry_point("load_seed")
    builder.add_edge("load_seed", "discover")
    builder.add_edge("discover", "pick_next")
    builder.add_conditional_edges("pick_next", _route_pick,
                                  {"score": "score", "__end__": END})
    builder.add_conditional_edges("score", _route_after_score, {
        "acquire_free": "acquire_free",
        "human_gate": "human_gate",
        "log_dropped": "log_dropped",
    })
    builder.add_conditional_edges("human_gate", _route_human, {
        "queue_ill": "queue_ill",
        "log_dropped": "log_dropped",
    })
    builder.add_edge("acquire_free", "pick_next")
    builder.add_edge("queue_ill", "pick_next")
    builder.add_edge("log_dropped", "pick_next")

    saver = checkpointer if checkpointer is not None else MemorySaver()
    return builder.compile(checkpointer=saver, interrupt_before=["human_gate"])


def initial_state(project: str, relevance_floor: int = 3,
                  media_sources: list[str] | None = None) -> DiscoveryState:
    return {
        "project": project, "seeds": [],
        "media_sources": media_sources if media_sources is not None else list(_DEFAULT_MEDIA_SOURCES),
        "candidates": [], "current": None,
        "relevance_floor": relevance_floor, "decision_override": None,
        "session_log": [], "acquired": 0, "queued": 0, "logged": 0,
    }


# ---------------------------------------------------------------------------
# Runner — the persistent service the user toggles on/off
# ---------------------------------------------------------------------------

def _scoped_projects(state_projects: list[str]) -> list[str]:
    """Discovery scope: the flag's project list, or all match-review-essay projects."""
    if state_projects:
        return state_projects
    import json as _json
    root = config.resolve_markery_root() or config.MARKERY_ROOT
    base = Path(root) / "projects"
    out: list[str] = []
    for d in sorted(base.iterdir()) if base.exists() else []:
        pj = d / "project.json"
        if pj.is_file():
            try:
                if _json.loads(pj.read_text()).get("type") == "match-review-essay":
                    out.append(d.name)
            except (ValueError, OSError):
                continue
    return out


def main() -> None:
    """Run one discovery tick per scoped project, only while the loop is enabled."""
    import sys
    root = config.resolve_markery_root() or config.MARKERY_ROOT
    if not root:
        print("MARKERY_ROOT not resolved.", file=sys.stderr)
        sys.exit(1)
    config.check_contract(root)

    status = tools.run_discovery_status()
    if not status.get("enabled"):
        print("discovery loop is OFF — enable with: markery historian discovery on")
        return

    floor = int(status.get("relevance_floor", 3))
    projects = sys.argv[1:] or _scoped_projects(status.get("projects", []))
    if not projects:
        print("No projects in discovery scope.")
        return

    saver = MemorySaver()
    graph = build_graph(saver)
    for project in projects:
        cfg = {"configurable": {"thread_id": f"discovery-{project}"}}
        final = graph.invoke(initial_state(project, floor), cfg)
        snap = graph.get_state(cfg)
        print(f"\n[{project}] acquired={final.get('acquired',0)} "
              f"queued={final.get('queued',0)} logged={final.get('logged',0)}")
        if snap.next:  # paused at human_gate
            cur = final.get("current") or {}
            print(f"  ⏸ awaiting human decision on ILL: {cur.get('title','')}")
            print(f"    {cur.get('worldcat_url','')}")
            print("    resume: queue or skip (interactive review).")


if __name__ == "__main__":
    main()
