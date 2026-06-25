# markery-langgraph — Project Contract

This file governs all Claude Code sessions in this repository.

---

## Constraints

- **No Claude attribution in commits** — do not add `Co-Authored-By` or any model credit line to commit messages
- **`MARKERY_ROOT` must be set** — all workflow runs and tests that invoke Markery CLI commands require `MARKERY_ROOT` pointing to the Markery repo root. Verify with `config.check_contract(os.environ["MARKERY_ROOT"])` before running.
- **Contract check before CLI calls** — any code that calls Markery subprocess commands must call `check_contract()` first. Never invoke `markery historian card`, `markery matchmaker confirm`, etc. without verifying the contract version matches.

---

## Repository Layout

```
src/langgraph_markery/
  config.py            — MARKERY_ROOT resolution, check_contract() (contract v1.2)
  state.py             — ResearchState TypedDict (candidate-review graph)
  tools.py             — subprocess wrappers (review + discovery commands)
  graph.py             — candidate-review graph (confirm/reject/defer + human_gate)
  discovery_state.py   — DiscoveryState TypedDict (discovery loop)
  discovery_graph.py   — Phase 30 discovery loop + runner main()
tests/
  test_graph.py            — review-graph integration tests (mocked tools)
  test_discovery_graph.py  — discovery-loop tests (mocked tools)
```

Two graphs, both shelling the Markery CLI (never importing markery):
- **Candidate review** (`graph.py`) — the original confirm/reject/defer workflow.
- **Discovery loop** (`discovery_graph.py`, Phase 30) — load_seed → discover →
  score → route: relevant+free → acquire_free; relevant+ILL → human_gate →
  queue_ill/drop; irrelevant → log_dropped. The **auto-acquire-free /
  gate-everything-else** boundary: free PD full text is acquired automatically;
  ILL (cost/commitment) interrupts for a human; every candidate is logged as a
  lead. Runs as a **persistent service the user toggles** via
  `markery historian discovery {on|off|status}` (a `library/discovery_state.json`
  flag the runner reads each tick).

---

## Running the workflow

```bash
python -m langgraph_markery.graph <project> [--review-all] [--reject-floor N]
```

`MARKERY_ROOT` no longer has to be exported manually. `config.resolve_markery_root()`
resolves it in this order (first hit with a `MANIFEST.json` wins):

1. the `MARKERY_ROOT` environment variable, if set;
2. a `.markery-root` pointer file in this repo root (first non-comment line is the path; relative paths resolve against the repo root);
3. a sibling `markery/` directory next to this repo.

Explicit export still works and takes precedence:

```bash
export MARKERY_ROOT=/path/to/markery
python -m langgraph_markery.graph <project>
```

### Reject review (`--review-all` / `--reject-floor`)

A model `reject` is surfaced at the `human_gate` (so a human can overturn it)
when `--review-all` is passed **or** the reject's score (1–5) is at or above the
reject-review floor (default `config.REJECT_REVIEW_FLOOR = 3`). Rejects below the
floor are auto-written to `rejected.jsonl`. Use `--review-all` to review every
recommendation — the mode that would have surfaced the Phase 22 P5 Mack bulldog
reject (score 1) for human override.

## Isolated environment

This repo runs in its own `.venv`, independent of the Markery venv (it never
imports `markery` as a library — it only shells out to the `markery` CLI). The
stdlib `python -m venv` cannot be used on this host (`ensurepip` /
`python3.12-venv` unavailable, `sudo` blocked), so create the env with
`virtualenv`, which bundles its own pip:

```bash
python3 -m virtualenv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Always invoke through the env (`.venv/bin/python`, `.venv/bin/pytest`) or activate it first.

## Running tests

```bash
cd markery-langgraph
.venv/bin/pytest
```

Tests use mocked tool calls — no live Markery CLI required. `MARKERY_ROOT` is not needed for the test suite. The 30-test suite runs entirely from the isolated `.venv`.

---

## Running the discovery loop (Phase 30)

The loop only acts while enabled (toggle lives in Markery):

```bash
markery historian discovery on               # in the Markery repo
.venv/bin/python -m langgraph_markery.discovery_graph   # one tick per scoped project
markery historian discovery off
```

A scheduler (cron) or a sleep-loop invokes the runner; each invocation is one
discovery tick that no-ops when the flag is off. Free PD acquisitions land
automatically; ILL candidates pause at `human_gate` for a queue/skip decision.

## Subprocess interface contract

This repo calls the Markery CLI commands declared in `$MARKERY_ROOT/MANIFEST.json`
(**contract v1.2** — review commands + the Phase 30 discovery surface: historian
relevance/discovery, librarian books/media-acquire/acquire/use/leads-add/wants-add).
Bump `contract_version` in `MANIFEST.json` whenever a command signature or output
format changes, then update `_EXPECTED_VERSION` in `config.py` to match.
