"""Subprocess wrappers that call the Markery CLI and parse its output.

All functions raise subprocess.CalledProcessError on non-zero exit unless
documented otherwise. MARKERY_ROOT must be in PATH or the markery command
must be resolvable from the caller's environment.
"""

from __future__ import annotations

import json
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
    """Run `markery historian card <project> <slug> --infer --json --out -`.

    Returns a dict with keys:
      card_text       str   — the full card markdown
      recommendation  str   — "confirm", "reject", or "defer"
      score           int   — 1–5
      reasoning       str   — one to three sentences from the model

    Uses the v1.1 `--json` contract (MANIFEST.json): stdout is a single JSON
    object, parsed directly. Falls back to the legacy stdout `[infer]`-block
    regex scrape if JSON parsing fails (e.g. an older Markery without --json),
    and to recommendation="defer", score=3 if neither yields a result.
    """
    cmd = ["markery", "historian", "card", project, slug, "--infer", "--json", "--out", "-"]
    if model:
        cmd += ["--model", model]

    result = subprocess.run(cmd, capture_output=True, text=True)
    stdout = result.stdout.strip()

    # Preferred path (v1.1 contract): stdout is one JSON object.
    try:
        obj = json.loads(stdout)
        return {
            "card_text":      str(obj.get("card_text", "")).strip(),
            "recommendation": str(obj.get("recommendation", "defer")).lower(),
            "score":          int(obj.get("score", 3)),
            "reasoning":      str(obj.get("reasoning", "")).strip(),
        }
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Legacy fallback: scrape the human-readable [infer] block.
    infer_marker = "\n[infer]"
    if infer_marker in result.stdout:
        card_text, infer_block = result.stdout.split(infer_marker, 1)
    else:
        card_text = result.stdout
        infer_block = ""
    rec_m   = re.search(r'recommendation=(\w+)', infer_block)
    score_m = re.search(r'score=([1-5])', infer_block)
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


# ---------------------------------------------------------------------------
# Discovery loop (Phase 30 — contract v1.2)
# ---------------------------------------------------------------------------

def run_discovery_status() -> dict:
    """Return the discovery on/off state (markery historian discovery status --json)."""
    result = subprocess.run(
        ["markery", "historian", "discovery", "status", "--json"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def run_books(query: str, max_results: int = 10) -> list[dict]:
    """Discover books via `markery librarian books <query> --json`.

    Returns a list of {title, author, year, isbn, action, ia_id, worldcat_url,
    ill_request}."""
    result = subprocess.run(
        ["markery", "librarian", "books", query, "--max-results", str(max_results), "--json"],
        capture_output=True, text=True, check=True,
    )
    out = result.stdout.strip()
    return json.loads(out.splitlines()[-1]) if out else []


def run_media_search(query: str, source: str = "commons",
                     max_results: int = 10) -> list[dict]:
    """Search a PD-media source (markery librarian media-search --json).

    Returns [{source, id}]. Returns [] on any failure (missing key, network) so
    one source's outage never breaks the discovery tick."""
    try:
        result = subprocess.run(
            ["markery", "librarian", "media-search", query, "--source", source,
             "--max-results", str(max_results), "--json"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return []
    out = result.stdout.strip()
    try:
        return json.loads(out.splitlines()[-1]) if out else []
    except json.JSONDecodeError:
        return []


def run_media_acquire(source: str, identifier: str, fair_use: bool = True) -> dict:
    """Acquire one media item (markery librarian media-acquire --json [--fair-use]).

    Returns {acquired: bool, slug?, license?, ...}. fair_use defaults True per the
    permissive non-commercial media policy."""
    cmd = ["markery", "librarian", "media-acquire", identifier, "--source", source, "--json"]
    if fair_use:
        cmd.append("--fair-use")
    result = subprocess.run(cmd, capture_output=True, text=True)
    out = result.stdout.strip()
    try:
        return json.loads(out.splitlines()[-1]) if out else {"acquired": False}
    except (json.JSONDecodeError, IndexError):
        return {"acquired": False}


def run_use(slug: str, project: str) -> bool:
    """Reference a global library item from a project (markery librarian use)."""
    result = subprocess.run(
        ["markery", "librarian", "use", slug, "--project", project],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def run_relevance(project: str, title: str, text: str = "") -> dict:
    """Score relevance via `markery historian relevance ... --json` → {score, reasoning}."""
    cmd = ["markery", "historian", "relevance", project, "--title", title, "--json"]
    if text:
        cmd += ["--text", text]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def run_acquire_text(ia_id: str) -> bool:
    """Acquire free full text from IA into library/works. True if it succeeded."""
    result = subprocess.run(
        ["markery", "librarian", "acquire", ia_id, "--source", "ia"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def run_wants_add(title: str, author: str = "", year: Optional[int] = None,
                  isbn: Optional[str] = None, ill_request: str = "") -> None:
    """Queue one ILL want (markery librarian wants-add)."""
    cmd = ["markery", "librarian", "wants-add", "--title", title]
    if author:
        cmd += ["--author", author]
    if year:
        cmd += ["--year", str(year)]
    if isbn:
        cmd += ["--isbn", isbn]
    if ill_request:
        cmd += ["--ill-request", ill_request]
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def run_leads_add(source: str, source_id: str, *, title: str = "", project: str = "",
                  relevance: Optional[int] = None, status: str = "logged",
                  note: str = "") -> None:
    """Log a discovery lead (markery librarian leads-add)."""
    cmd = ["markery", "librarian", "leads-add", "--source", source, "--id", source_id,
           "--status", status]
    if title:
        cmd += ["--title", title]
    if project:
        cmd += ["--project", project]
    if relevance is not None:
        cmd += ["--relevance", str(relevance)]
    if note:
        cmd += ["--note", note]
    subprocess.run(cmd, capture_output=True, text=True, check=True)


# ---------------------------------------------------------------------------
# Spawn pipeline (Phase 32 P4 — contract v1.4)
# ---------------------------------------------------------------------------

def run_seed_pairs(years: list[int]) -> list[dict]:
    """Scored mark↔patent seed pairs (markery matchmaker seed-pairs --years ... --json)."""
    cmd = ["markery", "matchmaker", "seed-pairs", "--years"] + [str(y) for y in years] + ["--json"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    out = result.stdout.strip()
    try:
        return json.loads(out.splitlines()[-1]) if out else []
    except (json.JSONDecodeError, IndexError):
        return []


def run_project_init(slug: str, ptype: str = "match-review-essay") -> bool:
    """Scaffold a project non-interactively (markery project init <slug> --type ...)."""
    result = subprocess.run(
        ["markery", "project", "init", slug, "--type", ptype],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def run_seed_project(slug: str, entity: str, industry: str = "") -> dict:
    """Scaffold entity files from a seed entity (markery matchmaker seed-project --json)."""
    cmd = ["markery", "matchmaker", "seed-project", slug, "--entity", entity, "--json"]
    if industry:
        cmd += ["--industry", industry]
    result = subprocess.run(cmd, capture_output=True, text=True)
    out = result.stdout.strip()
    try:
        return json.loads(out.splitlines()[-1]) if out else {}
    except (json.JSONDecodeError, IndexError):
        return {}


def run_build_entities(slug: str) -> bool:
    """Build the entity registry from a project's CSVs (markery matchmaker build --data-dir)."""
    result = subprocess.run(
        ["markery", "matchmaker", "build", "--data-dir", f"projects/{slug}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def run_match(slug: str) -> bool:
    """Generate candidate pairs for a project (markery match <slug>)."""
    result = subprocess.run(["markery", "match", slug], capture_output=True, text=True)
    return result.returncode == 0


def run_site_build(slug: str) -> bool:
    """Build a project's local site preview (markery site build <slug>)."""
    result = subprocess.run(["markery", "site", "build", slug], capture_output=True, text=True)
    return result.returncode == 0
