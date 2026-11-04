"""Baseline-aware continuation of a repository's canonical preflight (fail closed).

A product repository's own ``engineering/preflight.py`` stops at the first failing check.  When the machine that runs it
is red *before* the change (measured on VoiceStudio: ffmpeg, Colab, Docker and Electron tests fail on an untouched
``main``), no change can ever pass.  This module keeps the repository's gate and continues it past a pytest failure only
when the change introduces no failure that the untouched base commit does not already have:

* a failed pytest check passes only if every failing test id also fails on the base commit;
* every later check of the repository's own check list is still run, and any failure there other than a baseline-matched
  pytest failure fails the preflight;
* anything unreadable, truncated, over the id cap or not a pytest check fails closed.

The comparison never runs on a green preflight and never changes the repository's own script.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

MAX_IDS = 400
UNEXCUSABLE_IDS = frozenset({"control_plane", "exact_head_fence", "worktree_fence", "python_environment"})
_ID = re.compile(r"^(?:FAILED|ERROR) (.+?)(?: - .*)?\s*$", re.MULTILINE)
_SUMMARY = re.compile(r"\b(?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)\b.* in \d+(?:\.\d+)?s(?: \(\d+:\d{2}:\d{2}\))?[ =]*$")
_COUNT = re.compile(r"\b(\d+) (failed|errors?)\b")

#: ``run(argv, cwd, timeout_s) -> (returncode, full_output)``
Runner = Callable[[list[str], Path, int], tuple[int, str]]


def failing_ids(output: str) -> set[str] | None:
    """Complete failing node ids from pytest's short summary, or ``None`` when they cannot all be accounted for.

    Node ids may contain spaces (``test_x[with spaces]``), so the whole id up to pytest's `` - reason`` is taken, and the
    number of ``FAILED``/``ERROR`` lines must equal the failed + error counts in pytest's own final summary line.
    """
    found = _ID.findall(output)
    summary = [line for line in output.splitlines() if _SUMMARY.search(line)]
    if not summary:
        return None
    counted = sum(int(n) for n, _ in _COUNT.findall(summary[-1]))
    if counted != len(found) or len(set(found)) != len(found):
        return None
    return set(found)


def is_pytest(argv: list[str]) -> bool:
    return any(Path(part).stem.lower() == "pytest" for part in argv)


def ids_argv(argv: list[str], ids: list[str]) -> list[str]:
    """The repository's own pytest launcher followed by only the given ids."""
    at = next(i for i, part in enumerate(argv) if Path(part).stem.lower() == "pytest")
    return [*argv[: at + 1], *sorted(ids), "-q", "--tb=no", "-p", "no:cacheprovider"]


def judge_pytest(check: dict[str, Any], worktree: Path, base: Path, run: Runner) -> dict[str, Any]:
    """Rerun one failed pytest check uncapped, then compare its failing ids against the base copy."""
    argv = list(check["argv"])
    timeout = int(check.get("timeout_seconds", 3600))
    record: dict[str, Any] = {"id": check.get("id"), "baseline": "NOT_COMPARED", "ok": False}
    code, output = run(argv, worktree, timeout)
    if code == 0:
        return {**record, "baseline": "PASSED_ON_RERUN", "ok": True, "post_failures": 0}
    if code != 1:  # pytest: 1 = tests failed; 2 interrupted, 3 internal error, 4 usage, 5 none collected
        return {**record, "baseline": "PYTEST_DID_NOT_COMPLETE", "returncode": code}
    post = failing_ids(output)
    if not post:
        return {**record, "baseline": "NO_FAILING_IDS_PARSED"}
    if len(post) > MAX_IDS:
        return {**record, "baseline": "TOO_MANY_FAILURES", "post_failures": len(post)}
    base_code, base_output = run(ids_argv(argv, list(post)), base, timeout)
    if base_code not in (0, 1):
        return {**record, "baseline": "BASE_PYTEST_DID_NOT_COMPLETE", "base_returncode": base_code}
    at_base = failing_ids(base_output) if base_code == 1 else set()
    if at_base is None:
        return {**record, "baseline": "BASE_FAILURES_NOT_ACCOUNTED_FOR", "base_returncode": base_code}
    new = sorted(post - at_base)
    record.update(post_failures=len(post), base_failures=len(post & at_base), new_failures=new[:25],
                  base_returncode=base_code)
    if new:
        return {**record, "baseline": "NEW_FAILURES"}
    return {**record, "baseline": "MATCHES_BASE", "ok": True}


def continue_preflight(worktree: Path, base: Path, run: Runner, *, clean: Callable[[], bool]) -> dict[str, Any]:
    """Run the repository's own check list from the failed check onward, judging pytest failures against ``base``."""
    try:
        evidence = json.loads((worktree / "preflight-evidence.json").read_text(encoding="utf-8"))
        control = json.loads((worktree / "engineering" / "control-plane.json").read_text(encoding="utf-8"))
        checks = list(control["preflight"]["checks"])
        results = list(evidence["results"])
    except (OSError, ValueError, KeyError, TypeError):
        return {"ok": False, "verdict": "BASELINE_INPUTS_UNREADABLE", "checks": []}
    failed = [r for r in results if r.get("returncode") != 0]
    if len(failed) != 1 or failed[0].get("id") in UNEXCUSABLE_IDS:
        return {"ok": False, "verdict": "UNEXCUSABLE_FAILURE", "checks": [], "failed": [r.get("id") for r in failed]}
    order = [c.get("id") for c in checks]
    if failed[0].get("id") not in order or results[-1].get("id") != failed[0].get("id"):
        return {"ok": False, "verdict": "FAILED_CHECK_NOT_IN_CONTROL_PLANE", "checks": []}
    judged: list[dict[str, Any]] = []
    for check in checks[order.index(failed[0]["id"]):]:
        if check.get("id") == failed[0]["id"]:
            if not is_pytest(list(check["argv"])):
                return {"ok": False, "verdict": "FAILED_CHECK_IS_NOT_PYTEST", "checks": judged}
            verdict = judge_pytest(check, worktree, base, run)
        else:
            code, _ = run(list(check["argv"]), worktree, int(check.get("timeout_seconds", 3600)))
            if code == 0:
                verdict = {"id": check.get("id"), "baseline": "PASSED", "ok": True}
            elif is_pytest(list(check["argv"])):
                verdict = judge_pytest(check, worktree, base, run)
            else:
                verdict = {"id": check.get("id"), "baseline": "FAILED", "ok": False, "returncode": code}
        judged.append(verdict)
        if not verdict["ok"]:
            return {"ok": False, "verdict": "CHECK_FAILED", "checks": judged}
        if not clean():
            return {"ok": False, "verdict": "WORKTREE_CHANGED_DURING_VERIFICATION", "checks": judged}
    return {"ok": True, "verdict": "BASELINE_MATCH", "checks": judged}
