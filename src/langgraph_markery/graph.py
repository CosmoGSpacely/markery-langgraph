"""LangGraph review workflow for Markery patent-trademark research.

Entry point::

    python -m langgraph_markery.graph <project>

Nodes
-----
load_digest     — Read candidates.jsonl, filter unreviewed, build queue sorted by score.
pick_next       — Pop the next candidate; set current_slug. Terminates when queue is empty.
generate_card   — Write the card file to disk via `markery historian card`.
infer_card      — Call `run_card_infer`; store recommendation/score/reasoning in state.
route_infer     — Conditional edge: "confirm" → human_gate, "reject" → write_rejected,
                  "defer" → append_defer.
human_gate      — interrupt() surfaces card + recommendation for human approval.
                  On resume, caller passes recommendation_override="confirm"|"reject".
write_confirmed — Call run_confirm then run_draft; append slug to confirmed_this_session.
write_rejected  — Append candidate to rejected.jsonl.
append_defer    — Record slug in session_log for later review (no file write).

Edges
-----
load_digest → pick_next
pick_next → generate_card  (slug present)
pick_next → END            (queue empty)
generate_card → infer_card
infer_card → route_infer (conditional)
human_gate → write_confirmed | write_rejected (conditional on override)
write_confirmed → pick_next
write_rejected  → pick_next
append_defer    → pick_next
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from langgraph_markery import config
from langgraph_markery.state import ResearchState
from langgraph_markery.tools import run_card_infer, run_confirm, run_draft


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug_for(candidate: dict) -> str:
    tm = candidate.get("trademark") or "figurative"
    tm_slug = re.sub(r"[^a-z0-9]+", "-", tm.lower()).strip("-")
    return f"{tm_slug}-{candidate['patent_no'].lower()}"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def load_digest(state: ResearchState) -> ResearchState:
    project = state["project"]
    root = Path(config.MARKERY_ROOT)
    matches_dir = root / "projects" / project / "matches"

    candidates = _load_jsonl(matches_dir / "candidates.jsonl")
    confirmed = {c["slug"] for c in _load_jsonl(matches_dir / "confirmed.jsonl") if "slug" in c}
    rejected_slugs = {_slug_for(r) for r in _load_jsonl(matches_dir / "rejected.jsonl")}

    queue = []
    for c in candidates:
        slug = _slug_for(c)
        if slug not in confirmed and slug not in rejected_slugs:
            queue.append({**c, "_slug": slug})

    queue.sort(key=lambda x: x.get("score", 0), reverse=True)

    return {
        **state,
        "queue": queue,
        "confirmed_this_session": state.get("confirmed_this_session") or [],
        "session_log": (state.get("session_log") or []) + [
            f"load_digest: {len(queue)} unreviewed candidates"
        ],
    }


def pick_next(state: ResearchState) -> ResearchState:
    queue = list(state.get("queue") or [])
    if not queue:
        return {**state, "current_slug": None, "infer_result": None}
    candidate = queue.pop(0)
    slug = candidate["_slug"]
    return {
        **state,
        "queue": queue,
        "current_slug": slug,
        "infer_result": None,
        "recommendation_override": None,
        "session_log": state["session_log"] + [f"pick_next: → {slug}"],
    }


def generate_card(state: ResearchState) -> ResearchState:
    project = state["project"]
    slug = state["current_slug"]
    root = Path(config.MARKERY_ROOT)
    cards_dir = root / "projects" / project / "matches" / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    out_path = cards_dir / f"{slug}.md"

    result = subprocess.run(
        ["markery", "historian", "card", project, slug, "--out", str(out_path)],
        capture_output=True, text=True,
    )
    ok = result.returncode == 0
    return {
        **state,
        "session_log": state["session_log"] + [
            f"generate_card: {slug} → {'written' if ok else 'failed'}"
        ],
    }


def infer_card(state: ResearchState) -> ResearchState:
    project = state["project"]
    slug = state["current_slug"]
    infer = run_card_infer(project, slug)
    return {
        **state,
        "infer_result": infer,
        "session_log": state["session_log"] + [
            f"infer_card: {slug} → {infer['recommendation']} (score={infer['score']})"
        ],
    }


def human_gate(state: ResearchState) -> ResearchState:
    override = state.get("recommendation_override")
    if override in ("confirm", "reject"):
        decision = override
    else:
        decision = interrupt({
            "slug": state["current_slug"],
            "recommendation": state["infer_result"]["recommendation"],
            "score": state["infer_result"]["score"],
            "card_text": state["infer_result"]["card_text"],
            "reasoning": state["infer_result"]["reasoning"],
            "prompt": "Enter 'confirm' or 'reject':",
        })
        if not isinstance(decision, str) or decision not in ("confirm", "reject"):
            decision = "reject"

    updated_infer = {**state["infer_result"], "recommendation": decision}
    return {
        **state,
        "infer_result": updated_infer,
        "recommendation_override": None,
        "session_log": state["session_log"] + [
            f"human_gate: {state['current_slug']} → {decision}"
        ],
    }


def write_confirmed(state: ResearchState) -> ResearchState:
    project = state["project"]
    slug = state["current_slug"]
    run_confirm(project, slug, note=state["infer_result"].get("reasoning", "")[:200])
    _output, validated = run_draft(project, slug)
    confirmed = list(state["confirmed_this_session"]) + [slug]
    return {
        **state,
        "confirmed_this_session": confirmed,
        "session_log": state["session_log"] + [
            f"write_confirmed: {slug} confirmed; draft validated={validated}"
        ],
    }


def write_rejected(state: ResearchState) -> ResearchState:
    project = state["project"]
    slug = state["current_slug"]
    root = Path(config.MARKERY_ROOT)
    rejected_path = root / "projects" / project / "matches" / "rejected.jsonl"

    queue_all = _load_jsonl(root / "projects" / project / "matches" / "candidates.jsonl")
    record = next((c for c in queue_all if _slug_for(c) == slug), {"slug": slug})
    _append_jsonl(rejected_path, record)

    return {
        **state,
        "session_log": state["session_log"] + [f"write_rejected: {slug}"],
    }


def append_defer(state: ResearchState) -> ResearchState:
    slug = state["current_slug"]
    return {
        **state,
        "session_log": state["session_log"] + [f"append_defer: {slug} deferred for later review"],
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _route_infer(state: ResearchState) -> Literal["human_gate", "write_rejected", "append_defer"]:
    rec = (state.get("infer_result") or {}).get("recommendation", "defer")
    if rec == "confirm":
        return "human_gate"
    if rec == "reject":
        return "write_rejected"
    return "append_defer"


def _route_pick(state: ResearchState) -> Literal["generate_card", "__end__"]:
    return "generate_card" if state.get("current_slug") else "__end__"


def _route_human(state: ResearchState) -> Literal["write_confirmed", "write_rejected"]:
    rec = (state.get("infer_result") or {}).get("recommendation", "reject")
    return "write_confirmed" if rec == "confirm" else "write_rejected"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_graph(checkpointer=None):
    builder = StateGraph(ResearchState)

    builder.add_node("load_digest", load_digest)
    builder.add_node("pick_next", pick_next)
    builder.add_node("generate_card", generate_card)
    builder.add_node("infer_card", infer_card)
    builder.add_node("human_gate", human_gate)
    builder.add_node("write_confirmed", write_confirmed)
    builder.add_node("write_rejected", write_rejected)
    builder.add_node("append_defer", append_defer)

    builder.set_entry_point("load_digest")
    builder.add_edge("load_digest", "pick_next")

    builder.add_conditional_edges(
        "pick_next",
        _route_pick,
        {"generate_card": "generate_card", "__end__": END},
    )
    builder.add_edge("generate_card", "infer_card")
    builder.add_conditional_edges(
        "infer_card",
        _route_infer,
        {"human_gate": "human_gate", "write_rejected": "write_rejected", "append_defer": "append_defer"},
    )
    builder.add_conditional_edges(
        "human_gate",
        _route_human,
        {"write_confirmed": "write_confirmed", "write_rejected": "write_rejected"},
    )
    builder.add_edge("write_confirmed", "pick_next")
    builder.add_edge("write_rejected", "pick_next")
    builder.add_edge("append_defer", "pick_next")

    saver = checkpointer if checkpointer is not None else MemorySaver()
    return builder.compile(checkpointer=saver, interrupt_before=["human_gate"])


app = None  # Populated lazily so import doesn't require MARKERY_ROOT


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m langgraph_markery.graph <project>", file=sys.stderr)
        sys.exit(1)

    project = sys.argv[1]
    root = os.environ.get("MARKERY_ROOT", "")
    if not root:
        print("MARKERY_ROOT environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    config.check_contract(root)

    graph = build_graph()
    thread = {"configurable": {"thread_id": f"{project}-review"}}
    initial: ResearchState = {
        "project": project,
        "queue": [],
        "confirmed_this_session": [],
        "current_slug": None,
        "infer_result": None,
        "session_log": [],
        "recommendation_override": None,
    }

    for chunk in graph.stream(initial, config=thread):
        node, state = next(iter(chunk.items()))
        log = state.get("session_log", [])
        if log:
            print(log[-1])

        # Re-enter after interrupt to pass human decision
        snapshot = graph.get_state(thread)
        if snapshot.next == ("human_gate",):
            infer = snapshot.values.get("infer_result", {})
            print(f"\n[human_gate] {infer.get('recommendation','?')} (score={infer.get('score','?')})")
            print(infer.get("card_text", "")[:400])
            print(f"\nReasoning: {infer.get('reasoning','')}")
            decision = input("\nConfirm or reject? [confirm/reject]: ").strip().lower()
            if decision not in ("confirm", "reject"):
                decision = "reject"
            graph.update_state(thread, {"recommendation_override": decision})
            for chunk2 in graph.stream(None, config=thread):
                node2, state2 = next(iter(chunk2.items()))
                log2 = state2.get("session_log", [])
                if log2:
                    print(log2[-1])

    final = graph.get_state(thread).values
    print(f"\nSession complete. Confirmed: {final.get('confirmed_this_session', [])}")


if __name__ == "__main__":
    main()
