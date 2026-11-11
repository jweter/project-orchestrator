"""Run a repository's native preflight for a roadmap-progress commit, in its own process (issue #594).

The lane must never run a heavy preflight inside the supervisor turn (the heartbeat would stall), so the progress
writer spawns this runner exactly like a product worker: same persistent per-repository environment, same
supervised runtime, same release-bound execution authority check. It writes one JSON result bound to the exact
commit it verified; the writer publishes only if that result is ``ok`` for the commit it prepared.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

PREFLIGHT_TIMEOUT_S = 7200
EVIDENCE_LIMIT = 1500
FAILURE_MARKER = re.compile(r"Traceback|: error:|(?<![A-Za-z])(?:FAILED|FAIL|ERROR)(?![A-Za-z])")


def evidence_of(text: str, limit: int = EVIDENCE_LIMIT) -> str:
    """Keep the FIRST failure in a long preflight output, not just its tail.

    The tail of a long run is usually the summary line, which hides the check that actually failed. When the output
    fits, it is kept whole. Otherwise the text from the first failure marker is kept (about two thirds of the budget),
    then a short omitted-marker, then the very end (the summary). Nothing is invented: both parts are verbatim slices.
    """
    text = str(text or "")
    if len(text) <= limit:
        return text
    tail_budget = limit // 3
    head_budget = limit - tail_budget
    found = FAILURE_MARKER.search(text[: max(0, len(text) - tail_budget)])
    if found is None:
        return text[-limit:]
    start = max(0, found.start() - 80)
    head = text[start : start + head_budget]
    omitted = len(text) - len(head) - start - tail_budget
    if omitted <= 0:
        return text[-limit:]
    return f"{head}\n[... {omitted} characters omitted ...]\n{text[-tail_budget:]}"


ROOT =Path(__file__).resolve().parents[1]


def head_of(worktree: Path) -> str:
    out = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return out.stdout.strip()


def run(worktree: Path, result_path: Path, *, preflight: Callable[[Path, Callable[[], None], int], dict[str, Any]],
        authority: Callable[[], bool], head: Callable[[Path], str] = head_of) -> dict[str, Any]:
    """Verify ``worktree`` and write the result; a revoked authority or any error is a failed result, never a pass."""

    def check() -> None:
        if not authority():
            raise RuntimeError("EXECUTION_AUTHORITY_REVOKED")

    commit = head(worktree)
    try:
        check()
        outcome = preflight(worktree, check, PREFLIGHT_TIMEOUT_S)
        result = {"ok": bool(outcome.get("ok")), "returncode": outcome.get("returncode"),
                  "output_tail": evidence_of(outcome.get("output_tail", ""))}
    except Exception as exc:  # a failed run must be recorded, not raised into a silent hang
        result = {"ok": False, "returncode": None, "output_tail": f"{type(exc).__name__}: {str(exc)[:600]}"}
    if head(worktree) != commit:
        result.update(ok=False, output_tail="HEAD MOVED DURING VERIFICATION")
    result.update(head=commit, finished_at=datetime.now(UTC).isoformat())
    result_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = result_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2), encoding="utf-8")
    os.replace(tmp, result_path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Native preflight for a roadmap-progress commit.")
    parser.add_argument("--worktree", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    from project_orchestrator.execution_authority import evaluate_execution_authority

    spec = importlib.util.spec_from_file_location("iron_god_product_worker", ROOT / "scripts" / "iron_god_product_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    release = os.environ.get("IRON_GOD_ACCEPTED_SHA")

    def authority() -> bool:
        try:
            return evaluate_execution_authority(release_sha=release).permitted is True
        except Exception:
            return False

    run(args.worktree, args.result, preflight=worker.production_preflight, authority=authority)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
