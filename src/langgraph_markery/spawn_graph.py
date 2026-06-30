"""Phase 32 P4 — annual-review → project spawning loop.

  load_candidates  seed-pairs (years) → group by original applicant → drop entities
                   already in the dedup ledger.
  assemble         one proposal per entity: seed evidence + a coverage flag, sorted
                   into a clean (bulk-approve) tier and a review tier.
  ⛔ human_gate    ONE interrupt: the whole batch of proposals. Resume with a
                   {entity_key: approve|reject|defer} map. Nothing is created before
                   this; everything after executes the approved plan.
  spawn_approved   per approved entity: project init → seed-project → build → match →
                   all-source discovery preview (books/newspapers/media/images) →
                   auto-draft essay → LOCAL site build (preview). Records in the ledger.

Design locked 2026-06-28 (see markery ROADMAP P4): single gate at creation, tiered
batch approval with coverage flags, local preview only (never publishes — "go live"
is a separate human action), auto-drafted essays, all-source discovery. This module
only shells the markery CLI (never imports markery); contract v1.4.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from langgraph_markery import config, tools
from langgraph_markery.spawn_state import SpawnState

# All-source preview discovery: books (Open Library) are always searched; these add
# newspapers (chronam) and media/images (commons/loc/nara/dpla/ia).
_DEFAULT_MEDIA_SOURCES = ["commons", "loc", "nara", "ia", "chronam"]
_THIN_PAIRS = 3          # fewer than this many seed pairs → coverage flagged "thin"
_THIN_CPC = 2            # fewer than this many distinct CPC subclasses → "thin"
_CLEAN_SCORE = 0.8       # top seed-pair score at/above which a candidate can be "clean"
_BOOKS_PER_SEED = 8
_MEDIA_PER_SOURCE = 5


def _log(state: SpawnState, msg: str) -> None:
    state["session_log"].append(msg)


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "entity"


def _entity_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.upper()).strip()


# ---------------------------------------------------------------------------
# Dedup ledger (JSON; co-located under MARKERY_ROOT/library by default)
# ---------------------------------------------------------------------------

def _ledger_path(state: SpawnState) -> Path:
    p = state.get("ledger_path")
    if p:
        return Path(p)
    root = config.resolve_markery_root() or config.MARKERY_ROOT
    return Path(root) / "library" / "spawn_ledger.json"


def load_ledger(state: SpawnState) -> dict:
    path = _ledger_path(state)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {"entities": {}}
    return {"entities": {}}


def record_ledger(state: SpawnState, entity_key: str, slug: str) -> None:
    path = _ledger_path(state)
    ledger = load_ledger(state)
    ledger.setdefault("entities", {})[entity_key] = {"slug": slug}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def load_candidates(state: SpawnState) -> SpawnState:
    """seed-pairs over the years → one candidate per original applicant (deduped)."""
    pairs = tools.run_seed_pairs(state["years"])
    known = set(load_ledger(state).get("entities", {}))
    by_entity: dict[str, dict] = {}
    for p in pairs:
        applicant = p.get("applicant") or ""
        key = _entity_key(applicant)
        if not key or key in known:
            continue
        c = by_entity.setdefault(key, {
            "entity": applicant, "key": key, "slug": _slugify(applicant),
            "pairs": [], "is_tech": False})
        c["pairs"].append(p)
        c["is_tech"] = c["is_tech"] or bool(p.get("is_tech"))
    state["candidates"] = list(by_entity.values())
    _log(state, f"{len(state['candidates'])} new entity candidate(s) "
                f"from {len(pairs)} seed pairs ({len(known)} skipped via ledger)")
    return state


def assemble(state: SpawnState) -> SpawnState:
    """Per candidate: seed evidence + coverage flag + tier (clean | review)."""
    out = []
    for c in state["candidates"]:
        pairs = sorted(c["pairs"], key=lambda p: -p.get("score", 0))
        cpc = sorted({s for p in pairs for s in p.get("cpc", [])})
        top_score = pairs[0].get("score", 0) if pairs else 0
        # Local coverage heuristic (no network): thin evidence is a flag to review,
        # NOT proof of absence — true coverage needs an EPO check (corpus-bias finding).
        thin = len(pairs) < _THIN_PAIRS or len(cpc) < _THIN_CPC
        coverage = "thin" if thin else "ok"
        tier = "clean" if (coverage == "ok" and top_score >= _CLEAN_SCORE) else "review"
        out.append({
            "entity": c["entity"], "key": c["key"], "slug": c["slug"],
            "is_tech": c["is_tech"], "n_pairs": len(pairs),
            "top_score": round(top_score, 3), "cpc": cpc[:12],
            "top_pairs": [{"mark": p["mark"], "patent_no": p["patent_no"],
                           "score": p["score"], "cpc": p.get("cpc", [])}
                          for p in pairs[:5]],
            "coverage": coverage, "tier": tier,
        })
    out.sort(key=lambda c: (c["tier"] != "clean", -c["top_score"]))
    state["candidates"] = out
    n_clean = sum(1 for c in out if c["tier"] == "clean")
    _log(state, f"assembled {len(out)} proposal(s): {n_clean} clean, {len(out)-n_clean} review")
    return state


def human_gate(state: SpawnState) -> SpawnState:
    """ONE interrupt: the whole batch. Resume with a {entity_key: decision} map."""
    decisions = state.get("decisions")
    if not decisions:
        decisions = interrupt({
            "action": "spawn_batch",
            "proposals": state["candidates"],
            "prompt": "Approve/reject/defer each entity: {entity_key: approve|reject|defer}. "
                      "Unlisted entities default to defer.",
        })
        if not isinstance(decisions, dict):
            decisions = {}
    state["decisions"] = decisions
    return state


def _discover_preview(state: SpawnState, project: str, seeds: list[str]) -> dict:
    """All-source preview discovery for a spawned project (non-blocking).

    Books (Open Library) are relevance-scored; free PD acquired, ILL queued to wants
    (not gated inline). Media/newspapers/images are acquired under the fair-use policy
    and referenced into the project. Returns per-kind counters."""
    floor = state["relevance_floor"]
    counts = {"books": 0, "media": 0, "ill_queued": 0, "unscored": 0}
    for seed in seeds:
        for b in tools.run_books(seed, max_results=_BOOKS_PER_SEED):
            try:
                score = tools.run_relevance(project, b.get("title", "")).get("score")
                if score is None:           # model unavailable → skip, don't acquire blind
                    counts["unscored"] += 1
                    continue
                if int(score) < floor:
                    tools.run_leads_add("openlibrary", (b.get("title") or "x")[:60],
                                        title=b.get("title", ""), project=project,
                                        relevance=score, status="dropped")
                    continue
                if b.get("action") == "acquire" and b.get("ia_id"):
                    if tools.run_acquire_text(b["ia_id"]):
                        counts["books"] += 1
                    tools.run_leads_add("openlibrary", b["ia_id"], title=b.get("title", ""),
                                        project=project, relevance=score, status="acquired")
                else:   # ILL — queue a want, do not gate inline
                    tools.run_wants_add(b.get("title", ""), author=b.get("author", ""),
                                        year=b.get("year"), isbn=b.get("isbn"),
                                        ill_request=b.get("ill_request", ""))
                    counts["ill_queued"] += 1
            except Exception as exc:        # one bad item never aborts the pass
                _log(state, f"book skip ({b.get('title','')[:30]}): {str(exc)[:60]}")
        for src in state.get("media_sources", _DEFAULT_MEDIA_SOURCES):
            for hit in tools.run_media_search(seed, source=src, max_results=_MEDIA_PER_SOURCE):
                try:
                    res = tools.run_media_acquire(hit["source"], hit["id"], fair_use=True)
                    if res.get("acquired"):
                        if res.get("slug"):
                            tools.run_use(res["slug"], project)
                        counts["media"] += 1
                    tools.run_leads_add(hit["source"], hit["id"], project=project,
                                        status="acquired" if res.get("acquired") else "logged",
                                        note=f"media {res.get('license','')}".strip())
                except Exception as exc:
                    _log(state, f"media skip ({hit.get('id','')}): {str(exc)[:60]}")
    return counts


def _auto_draft(state: SpawnState, project: str, top_pairs: list[dict]) -> bool:
    """Best-effort: confirm the strongest seed pair, scaffold it, then draft the
    essay (free model). Tries successive pairs until one confirms; scaffold is
    deterministic and required before draft."""
    for p in top_pairs:
        slug = f"{_slugify(p['mark'])}-{p['patent_no'].lower()}"
        try:
            tools.run_confirm(project, slug, note="auto-spawned preview seed")
        except Exception:
            continue                      # pair not a confirmable candidate; try next
        if not tools.run_scaffold(project, slug):
            continue
        _, ok = tools.run_draft(project, slug)   # draft degrades to ok=False on model outage
        if ok:
            _log(state, f"essay drafted: {slug}")
        return ok
    return False


def spawn_approved(state: SpawnState) -> SpawnState:
    """Execute the approved plan per entity → a local preview project."""
    decisions = state["decisions"]
    for c in state["candidates"]:
        decision = decisions.get(c["key"], "defer")
        if decision == "reject":
            state["rejected"].append(c["key"]); _log(state, f"rejected: {c['entity']}")
            continue
        if decision != "approve":
            state["deferred"].append(c["key"]); _log(state, f"deferred: {c['entity']}")
            continue
        slug = c["slug"]
        if not tools.run_project_init(slug):
            _log(state, f"FAILED init: {slug}"); continue
        tools.run_seed_project(slug, c["entity"])
        # Guard: an unregistered entity → match yields 0 candidates → empty site.
        if not tools.run_build_entities(slug):
            state["rejected"].append(c["key"])
            _log(state, f"FAILED build-entities (skipping): {slug}"); continue
        if not tools.run_match(slug):
            _log(state, f"WARN match non-zero: {slug}")
        disc = _discover_preview(state, slug, [c["entity"]])
        drafted = _auto_draft(state, slug, c["top_pairs"])
        built = tools.run_site_build(slug)
        record_ledger(state, c["key"], slug)
        rec = {"entity": c["entity"], "slug": slug, "drafted": drafted,
               "site_built": built, **disc}
        state["spawned"].append(rec)
        _log(state, f"spawned: {slug}  (books {disc['books']}, media {disc['media']}, "
                    f"essay {'✓' if drafted else '—'}, site {'✓' if built else '—'})")
    return state


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def build_graph(checkpointer=None):
    b = StateGraph(SpawnState)
    b.add_node("load_candidates", load_candidates)
    b.add_node("assemble", assemble)
    b.add_node("human_gate", human_gate)
    b.add_node("spawn_approved", spawn_approved)
    b.set_entry_point("load_candidates")
    b.add_edge("load_candidates", "assemble")
    b.add_edge("assemble", "human_gate")
    b.add_edge("human_gate", "spawn_approved")
    b.add_edge("spawn_approved", END)
    saver = checkpointer if checkpointer is not None else MemorySaver()
    return b.compile(checkpointer=saver, interrupt_before=["human_gate"])


def initial_state(years: list[int], *, relevance_floor: int = 3,
                  media_sources: list[str] | None = None,
                  ledger_path: str | None = None) -> SpawnState:
    return {
        "years": years,
        "media_sources": media_sources if media_sources is not None else list(_DEFAULT_MEDIA_SOURCES),
        "relevance_floor": relevance_floor,
        "ledger_path": ledger_path or "",
        "candidates": [], "decisions": {},
        "spawned": [], "rejected": [], "deferred": [], "session_log": [],
    }
