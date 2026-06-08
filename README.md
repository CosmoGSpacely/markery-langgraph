# markery-langgraph

LangGraph review workflow for [Markery](https://github.com/CosmoGSpacely/markery) patent-trademark research.

Automates the candidate review cycle: loads the project queue, runs `historian card --infer` for each candidate, routes on the model's recommendation (confirm / reject / defer), writes confirmed pairs via `matchmaker confirm`, and drafts essays via `historian draft`. Human review is surfaced via LangGraph's `interrupt()` mechanism before any confirmation is written.

## Requirements

- Python ≥ 3.11
- A working Markery installation with `markery` on PATH
- `MARKERY_ROOT` environment variable pointing to the Markery repo root

## Setup

```bash
# Clone alongside the Markery repo
git clone https://github.com/CosmoGSpacely/markery-langgraph.git
cd markery-langgraph
pip install -e .

# Set the Markery root
export MARKERY_ROOT=/path/to/markery
```

## Contract check

Before running any workflow, verify that the installed Markery version exposes the expected subprocess interface:

```bash
python -c "
from langgraph_markery import config
config.check_contract('$MARKERY_ROOT')
print('Contract OK')
"
```

This reads `MANIFEST.json` from `MARKERY_ROOT` and asserts `contract_version == "1.0"`. If the version does not match, update either this package or Markery before proceeding.

## Subprocess interface

The workflow calls four Markery CLI commands:

| Command | Used by |
|---|---|
| `markery historian card <project> <slug> --infer` | `run_card_infer()` |
| `markery historian digest <project>` | `run_digest()` |
| `markery historian draft <project> <slug>` | `run_draft()` |
| `markery matchmaker confirm <project> <slug>` | `run_confirm()` |

## Running the graph (Phase 21 P2)

The full LangGraph graph is implemented in `src/langgraph_markery/graph.py` (Phase 21 P2). Once available:

```bash
python -m langgraph_markery.graph <project>
```

To resume after a `human_gate` interrupt, inject an override recommendation:

```python
from langgraph_markery.graph import app
app.invoke({"recommendation_override": "confirm"}, config={"configurable": {"thread_id": "..."}})
```
