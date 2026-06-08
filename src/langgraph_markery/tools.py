"""Subprocess wrappers that call the Markery CLI and parse its output.

All functions raise subprocess.CalledProcessError on non-zero exit unless
documented otherwise. MARKERY_ROOT must be in PATH or the markery command
must be resolvable from the caller's environment.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional


def run_digest(project: str) -> str:
    """Run `markery historian digest <project>` and return stdout."""
    result = subprocess.run(
        ["markery", "historian", "digest", project],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def run_card_infer(
    project: str,
    slug: str,
    model: Optional[str] = None,
) -> dict:
    """Run `markery historian card <project> <slug> --infer --out -`.

    Returns a dict with keys:
      card_text       str   — the full card markdown (before the [infer] block)
      recommendation  str   — "confirm", "reject", or "defer"
      score           int   — 1–5
      reasoning       str   — one to three sentences from the model
    Falls back to recommendation="defer", score=3 if the [infer] block is absent
    or unparseable (e.g. model quota exceeded).
    """
    cmd = ["markery", "historian", "card", project, slug, "--infer", "--out", "-"]
    if model:
        cmd += ["--model", model]

    result = subprocess.run(cmd, capture_output=True, text=True)
    stdout = result.stdout

    # Split card text from the [infer] block.
    # The historian CLI prints "\n[infer]  recommendation=X  score=Y\n         reasoning"
    # to stdout after the card text.
    infer_marker = "\n[infer]"
    if infer_marker in stdout:
        card_text, infer_block = stdout.split(infer_marker, 1)
    else:
        card_text = stdout
        infer_block = ""

    rec_m   = re.search(r'recommendation=(\w+)', infer_block)
    score_m = re.search(r'score=([1-5])', infer_block)
    # Reasoning is on the second line of the infer block (indented with spaces)
    reason_lines = [
        ln.strip() for ln in infer_block.splitlines()
        if ln.strip() and not ln.strip().startswith("recommendation=")
    ]
    reasoning = " ".join(reason_lines).strip()

    return {
        "card_text":      card_text.strip(),
        "recommendation": rec_m.group(1).lower() if rec_m else "defer",
        "score":          int(score_m.group(1)) if score_m else 3,
        "reasoning":      reasoning or infer_block.strip(),
    }


def run_confirm(
    project: str,
    slug: str,
    note: Optional[str] = None,
) -> None:
    """Run `markery matchmaker confirm <project> <slug> [--note NOTE]`.

    Raises subprocess.CalledProcessError if the pair is not found or confirm fails.
    """
    cmd = ["markery", "matchmaker", "confirm", project, slug]
    if note:
        cmd += ["--note", note]
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def run_draft(project: str, slug: str) -> tuple[str, bool]:
    """Run `markery historian draft <project> <slug>`.

    Returns (stdout, validate_passed) where validate_passed is True when the
    command exits 0 (draft written and immediately validated by the historian).
    stdout contains the draft path and validation summary.
    """
    result = subprocess.run(
        ["markery", "historian", "draft", project, slug],
        capture_output=True, text=True,
    )
    validate_passed = result.returncode == 0
    return result.stdout + result.stderr, validate_passed
