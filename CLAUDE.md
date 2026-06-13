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
  config.py    — MARKERY_ROOT resolution, check_contract()
  state.py     — ResearchState TypedDict
  tools.py     — subprocess wrappers (run_digest, run_card_infer, run_confirm, run_draft)
  graph.py     — LangGraph workflow graph (nodes, edges, human_gate interrupt)
tests/
  test_graph.py  — integration tests with mocked tool calls
```

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

## Running tests

```bash
cd markery-langgraph
pytest
```

Tests use mocked tool calls — no live Markery CLI required. `MARKERY_ROOT` is not needed for the test suite.

---

## Subprocess interface contract

This repo calls four Markery CLI commands declared in `$MARKERY_ROOT/MANIFEST.json`. Bump `contract_version` in `MANIFEST.json` whenever a command signature or output format changes, then update `check_contract()` in `config.py` to assert the new version.
