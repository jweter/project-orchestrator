"""Governed, free-only Everward UE5.8 verification adapter.

Existing owner control, exclusive worker leases, capability admission and
exact-head release rules remain authoritative. This module neither creates
authority nor starts Unreal through a scheduled task by itself.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from .execution_runtime import RuntimeRequest

EVERWARD = "jweter/Project-Everward"
SCRIPT = Path("tools/unreal_free_agent_stack.py")
SUITES = frozenset(("ubt", "automation", "gauntlet", "package"))
PREFIX_RE = re.compile(r"^[A-Za-z0-9_.]{1,100}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TIMEOUTS = {"ubt": 1200, "automation": 1200, "gauntlet": 1200, "package": 2400}


def make_native_request(
    job: dict[str, Any], *,
    checkout: Path, python_exe: Path, engine: Path,
    evidence_dir: Path, suite: str = "automation", test: str = "Everward",
) -> RuntimeRequest:
    """Prepare, never execute, a scoped native test against a known checkout.

    Explicit approved lease and owner callback are required separately by
    execute_authorized. Actual laptop admission and linked-worktree checks
    are also enforced by the Everward-side runner.
    """
    if job.get("repository") != EVERWARD:
        raise ValueError("ONLY_EVERWARD_NATIVE_SUITE")
    if job.get("purpose") != "product_reality_evidence_collection":
        raise ValueError("UNAUTHORIZED_PURPOSE")
    if not isinstance(job.get("base_sha"), str) or not SHA_RE.fullmatch(job["base_sha"]):
        raise ValueError("EXACT_HEAD_REQUIRED")
    if suite not in SUITES:
        raise ValueError("UNREVIEWED_NATIVE_SUITE")
    if suite in {"automation", "gauntlet"} and not PREFIX_RE.fullmatch(test or ""):
        raise ValueError("INVALID_NATIVE_TEST_NAME")
    checkout = checkout.resolve()
    script = checkout / SCRIPT
    if not script.is_file():
        raise ValueError("APPROVED_EVERWARD_RUNNER_NOT_INSTALLED")
    if not python_exe.is_file():
        raise ValueError("PYTHON_EXECUTABLE_MISSING")
    if evidence_dir.resolve().is_relative_to(checkout):
        raise ValueError("EVIDENCE_MUST_BE_OUTSIDE_WORKTREE")
    argv = (
        str(python_exe.resolve()), str(script),
        "--mode", "run-native", "--root", str(checkout),
        "--engine", str(engine.resolve()), "--kind", suite,
        "--evidence-dir", str(evidence_dir.resolve()),
        "--ack-isolated-worktree",
    )
    if test:
        argv += ("--test", test)
    return RuntimeRequest(argv=argv, cwd=checkout, timeout_seconds=TIMEOUTS[suite])


def execute_authorized(
    job: dict[str, Any], *,
    checkout: Path, python_exe: Path, engine: Path,
    evidence_dir: Path, authority_check: Callable[[], None],
    runtime: Any, suite: str = "automation", test: str = "Everward",
) -> dict[str, Any]:
    """Invoke through the existing supervised Windows runtime.

    Caller must enforce the actual Take-the-Wheel owner grant, worker lease,
    admission, and exact-head match. The adapter cannot mint any of them.
    """
    if not callable(authority_check):
        raise ValueError("LIVE_AUTHORITY_CALLBACK_REQUIRED")
    authority_check()
    request = make_native_request(
        job, checkout=checkout, python_exe=python_exe,
        engine=engine, evidence_dir=evidence_dir, suite=suite, test=test)
    # The runtime checks continuing authority during execution and terminates
    # its owned Windows process tree when the grant is revoked.
    result = runtime.execute_supervised(request, authority_check)
    authority_check()
    if result.returncode != 0:
        return {"status": "FAIL", "head": job["base_sha"],
                "native_process_returncode": result.returncode,
                "merge_authority": False}
    try:
        evidence = json.loads(result.stdout)
        if not isinstance(evidence, dict):
            raise ValueError("non-object evidence")
    except (ValueError, TypeError):
        return {"status": "REVIEW_REQUIRED",
                "reason": "NATIVE_EVIDENCE_UNPARSABLE", "merge_authority": False}
    if evidence.get("head") != job["base_sha"]:
        return {"status": "REVIEW_REQUIRED",
                "reason": "EXACT_HEAD_MISMATCH", "merge_authority": False}
    # Never turn native process success into player-experience or merge approval.
    if evidence.get("status") not in {
        "REVIEW_REQUIRED", "RESOURCE_WAIT", "FAIL", "ENVIRONMENT_FAILURE"
    }:
        return {"status": "REVIEW_REQUIRED",
                "reason": "UNKNOWN_NATIVE_VERDICT", "merge_authority": False}
    return {**evidence, "merge_authority": False,
            "product_reality_verified": False}
