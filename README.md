# markery-langgraph

LangGraph review workflow for [Markery](https://github.com/CosmoGSpacely/markery) patent-trademark research.

Automates the candidate review cycle: loads the project queue, runs `historian card --infer` for each candidate, routes on the model's recommendation (confirm / reject / defer), writes confirmed pairs via `matchmaker confirm`, and drafts essays via `historian draft`. Human review is surfaced via LangGraph's `interrupt()` mechanism before any confirmation is written.

## Requirements

- Python ≥ 3.11
- A working Markery installation with `markery` on PATH
- `MARKERY_ROOT` environment variable pointing to the Markery repo root

## Setup

This repo runs in its **own isolated virtual environment**, independent of the
Markery venv — nothing here imports `markery` as a library; it only shells out to
the `markery` CLI. Create the environment with `virtualenv`, which bundles its own
pip (the stdlib `python -m venv` cannot be used on this host: `ensurepip` /
`python3.12-venv` is unavailable and installing it needs `sudo`).

```bash
# Clone alongside the Markery repo (the sibling layout lets MARKERY_ROOT auto-resolve)
git clone https://github.com/CosmoGSpacely/markery-langgraph.git
cd markery-langgraph

# Create the isolated env with virtualenv (bundles pip; works without ensurepip)
python3 -m pip install --user virtualenv     # once, if virtualenv is not present
python3 -m virtualenv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

`MARKERY_ROOT` no longer has to be exported — `config.resolve_markery_root()`
finds the sibling `markery/` directory automatically (see "Markery root
resolution" below). An explicit `export MARKERY_ROOT=/path/to/markery` still works
and takes precedence.

Run everything through the env's interpreter (`.venv/bin/python`, `.venv/bin/pytest`)
or `source .venv/bin/activate` first.

## Contract check

Before running any workflow, verify that the installed Markery version exposes the expected subprocess interface:

```bash
python -c "
from langgraph_markery import config
config.check_contract('$MARKERY_ROOT')
print('Contract OK')
"
```

This reads `MANIFEST.json` from `MARKERY_ROOT` and asserts `contract_version == "1.1"`. If the version does not match, update either this package or Markery before proceeding.

## Subprocess interface

The workflow calls four Markery CLI commands:

| Command | Used by |
|---|---|
| `markery historian card <project> <slug> --infer` | `run_card_infer()` |
| `markery historian digest <project>` | `run_digest()` |
| `markery historian draft <project> <slug>` | `run_draft()` |
| `markery matchmaker confirm <project> <slug>` | `run_confirm()` |

## Running the graph

```bash
export MARKERY_ROOT=/path/to/markery
python -m langgraph_markery.graph <project>
```

The graph loads all unreviewed candidates for `<project>`, runs `historian card --infer` on each, and routes on the model's recommendation:

- **confirm** → pauses at `human_gate` for approval, then calls `matchmaker confirm` and `historian draft`
- **reject** → appends the candidate to `rejected.jsonl`
- **defer** → logs the slug for later review (no file write)

The graph runs until the candidate queue is empty. Confirmed slugs are printed at the end.

### Human gate

When the graph pauses at `human_gate`, the CLI prompts interactively. To drive the graph programmatically, build a graph with a persistent checkpointer and resume with an override:

```python
from langgraph.checkpoint.memory import MemorySaver
from langgraph_markery.graph import build_graph

graph = build_graph(checkpointer=MemorySaver())
thread = {"configurable": {"thread_id": "radio-pioneers-review"}}

# Run until interrupt
list(graph.stream(initial_state, config=thread))

# Inspect the interrupted state
snapshot = graph.get_state(thread)
print(snapshot.values["infer_result"])

# Resume with a human decision
graph.update_state(thread, {"recommendation_override": "confirm"})
list(graph.stream(None, config=thread))
```

`recommendation_override` accepts `"confirm"` or `"reject"`. Any other value defaults to `"reject"`.

## Markery root resolution

`config.resolve_markery_root()` resolves the Markery repo root in this order
(first hit with a `MANIFEST.json` wins):

1. the `MARKERY_ROOT` environment variable, if set;
2. a `.markery-root` pointer file in this repo root (first non-comment line is the
   path; relative paths resolve against the repo root);
3. a sibling `markery/` directory next to this repo.

## Running tests

```bash
cd markery-langgraph
.venv/bin/pytest        # or: source .venv/bin/activate && pytest
```

Tests use mocked tool calls — no live Markery CLI or `MARKERY_ROOT` required.
The 30-test suite runs entirely from the isolated `.venv`.
