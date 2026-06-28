"""Configuration and contract validation for langgraph-markery.

MARKERY_ROOT must be set in the environment before running the graph.
check_contract(root) verifies the Markery repo at root exposes the expected
subprocess interface version.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MARKERY_ROOT: str = os.environ.get("MARKERY_ROOT", "")

_EXPECTED_VERSION = "1.3"

# A model `reject` whose score (1–5) is at or above this floor is surfaced to the
# human_gate rather than auto-written to rejected.jsonl. Rejects below the floor
# are confident "clearly not a match" calls and are auto-rejected. Raise the
# floor (or pass --review-all) to surface more rejects for human override.
REJECT_REVIEW_FLOOR = 3


def resolve_markery_root() -> str:
    """Resolve the Markery repo root without requiring a manual export.

    Resolution order (first hit wins):
      1. ``MARKERY_ROOT`` environment variable, if set and non-empty.
      2. A ``.markery-root`` pointer file in this repo root containing the path
         (first non-empty, non-comment line).
      3. A sibling ``markery/`` directory next to this repo.

    A candidate is accepted only if it contains a ``MANIFEST.json``. Returns the
    resolved absolute path as a string, or "" if none could be found.
    """
    def _ok(p: Path) -> bool:
        return (p / "MANIFEST.json").exists()

    env = os.environ.get("MARKERY_ROOT", "").strip()
    if env:
        return env  # honour an explicit export even if MANIFEST is missing — check_contract reports that

    repo_root = Path(__file__).resolve().parents[2]  # src/langgraph_markery/config.py → repo root

    pointer = repo_root / ".markery-root"
    if pointer.exists():
        for line in pointer.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                candidate = Path(line).expanduser()
                if not candidate.is_absolute():
                    candidate = (repo_root / candidate).resolve()
                if _ok(candidate):
                    return str(candidate)
                break

    sibling = repo_root.parent / "markery"
    if _ok(sibling):
        return str(sibling.resolve())

    return ""


def check_contract(root: str | Path) -> None:
    """Read MANIFEST.json from root and assert contract_version == '1.1'.

    Raises RuntimeError if the file is missing or the version does not match.
    Call this once at process startup before invoking any subprocess tools.
    """
    manifest_path = Path(root) / "MANIFEST.json"
    if not manifest_path.exists():
        raise RuntimeError(
            f"MANIFEST.json not found at {manifest_path}. "
            "Is MARKERY_ROOT set to the correct Markery repo directory?"
        )
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Could not read MANIFEST.json: {exc}") from exc

    version = data.get("contract_version")
    if version != _EXPECTED_VERSION:
        raise RuntimeError(
            f"Markery contract version mismatch: expected {_EXPECTED_VERSION!r}, "
            f"got {version!r}. Update langgraph-markery or Markery to align versions."
        )
