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

_EXPECTED_VERSION = "1.0"


def check_contract(root: str | Path) -> None:
    """Read MANIFEST.json from root and assert contract_version == '1.0'.

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
