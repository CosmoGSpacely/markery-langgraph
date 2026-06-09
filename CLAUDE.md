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
export MARKERY_ROOT=/path/to/markery
python -m langgraph_markery.graph <project>
```

## Running tests

```bash
cd markery-langgraph
pytest
```

Tests use mocked tool calls — no live Markery CLI required. `MARKERY_ROOT` is not needed for the test suite.

---

## Subprocess interface contract

This repo calls four Markery CLI commands declared in `$MARKERY_ROOT/MANIFEST.json`. Bump `contract_version` in `MANIFEST.json` whenever a command signature or output format changes, then update `check_contract()` in `config.py` to assert the new version.
