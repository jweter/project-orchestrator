"""Durable Godot scheduling: one authorized local worker, no concurrent model.

This lane only dispatches graph-declared, owner-authorized GDScript work. It
doesn't grant work authority, replace the source graph, or waive CI. An
unreconciled worker is a blocker, never permission to launch a duplicate.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from . import iron_god_gdscript_builder as builder
from . import iron_god_cpp_builder as bounded
from . import iron_god_cpp_lane as native
from .state_io import atomic_json

MAX_ATTEMPTS = 2
JOB_DIR = "godot-jobs"
HEX = re.compile(r"^[0-9a-f]{40}$")


def eligible(request: dict[str, Any], *, repository: str, base: str) -> bool:
    """A compiled request is necessary; mere keywords never authorize execution."""
    return (isinstance(request, dict)
            and request.get("repository") == repository
            and request.get("exact_head") == base
            and bool(HEX.fullmatch(base))
            and request.get("owner_approved") is True
            and isinstance(request.get("gdscript"), dict)
            and request.get("capability_level") == 1
            and isinstance(request.get("route"), dict)
            and request["route"].get("builder") == "LOCAL"
            and not request.get("reserved_conflicts")
            and isinstance(request.get("id"), str) and bool(request["id"]))


def job_path(root: Path, request: dict[str, Any]) -> Path:
    ident = hashlib.sha256(json.dumps([request["repository"], request["id"]], separators=(",", ":")).encode()).hexdigest()[:24]
    return Path(root) / JOB_DIR / (ident + ".json")


def read_job(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    try:
        item = json.loads(job_path(root, request).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"id": request["id"], "repository": request["repository"], "state": "NEW", "attempts": 0}
    return item if isinstance(item, dict) else {"state": "NEEDS_RECONCILIATION"}


def in_progress(root: Path) -> bool:
    folder = Path(root) / JOB_DIR
    for path in folder.glob("*.json") if folder.is_dir() else []:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        if not isinstance(job, dict) or job.get("state") in {"STARTING", "RUNNING", "NEEDS_RECONCILIATION"}:
            return True
    return False


def advance(
    root: Path, request: dict[str, Any], *, base: str, clone: Path,
    may_start: bool, reserve: Callable[[], str | None],
    release: Callable[[str], None],
    spawn: Callable[[Path, Path, Path, str], dict[str, Any] | None],
    alive: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    """Start one release-bound author or harvest its result, never replay blindly."""
    if not eligible(request, repository=request.get("repository", ""), base=base):
        return {"status": "NOT_AUTHORIZED"}
    path = job_path(root, request)
    job = read_job(root, request)
    if job.get("id") != request["id"] or job.get("repository") != request["repository"]:
        return {"status": "NEEDS_RECONCILIATION", "reason": "JOB_IDENTITY_CHANGED"}
    if job.get("base") and job["base"] != base:
        return {"status": "NEEDS_RECONCILIATION", "reason": "BASE_HEAD_CHANGED"}
    if job.get("state") in {"STARTING", "NEEDS_RECONCILIATION"}:
        return {"status": "NEEDS_RECONCILIATION", "reason": "UNCERTAIN_WORKER_IDENTITY"}
    if job.get("state") == "RUNNING":
        result_file = Path(job["result_path"])
        if not result_file.is_file():
            if alive(job.get("worker") or {}):
                return {"status": "RUNNING", "id": request["id"]}
            # Never dispatch another copy after a possibly incomplete launch.
            job["state"] = "NEEDS_RECONCILIATION"
            atomic_json(path, job)
            return {"status": "NEEDS_RECONCILIATION", "reason": "WORKER_EXITED_WITHOUT_RESULT"}
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"status": "NEEDS_RECONCILIATION", "reason": "UNREADABLE_WORKER_RESULT"}
        if (not isinstance(result, dict) or result.get("request") != request["id"]
                or result.get("base") != base):
            return {"status": "NEEDS_RECONCILIATION", "reason": "RESULT_IDENTITY_MISMATCH"}
        if alive(job.get("worker") or {}):
            return {"status": "RUNNING", "reason": "WORKER_STILL_ACTIVE"}
        job["result"] = result
        job["state"] = "BUILT" if result.get("status") == "BUILT_AND_VERIFIED" else "FAILED"
        # Memory wait is not a code failure, and cannot consume an attempt.
        if result.get("status") == "WAITING_FOR_MEMORY":
            job["state"] = "WAITING"
            job["attempts"] = max(0, int(job["attempts"]) - 1)
        atomic_json(path, job)
        if job.get("lease_id"):
            release(str(job["lease_id"]))
        return {"status": job["state"], "id": request["id"], "reason": result.get("reason", "")}
    if job.get("state") in {"BUILT", "PUBLISHED", "FAILED"}:
        return {"status": job["state"], "id": request["id"]}
    if not may_start or in_progress(root):
        return {"status": "WAITING_FOR_OTHER_LOCAL_WORK"}
    if int(job.get("attempts", 0)) >= MAX_ATTEMPTS:
        return {"status": "ATTEMPTS_EXHAUSTED"}
    if builder.validate(request, clone) or builder.toolchain_problems():
        return {"status": "NOT_READY", "reason": "GDSCRIPT_SCOPE_OR_TOOLCHAIN_UNVERIFIED"}
    lease_id = reserve()
    if not lease_id:
        return {"status": "WAITING_FOR_RUNTIME_LEASE"}
    path.parent.mkdir(parents=True, exist_ok=True)
    request_file = path.with_suffix(".request.json")
    result_file = path.with_suffix(".result.json")
    # State is durable *before* Popen. A crash here requires owner-safe
    # reconciliation instead of creating a second potentially live worker.
    job.update(state="STARTING", base=base, lease_id=lease_id,
               result_path=str(result_file))
    atomic_json(path, job)
    atomic_json(request_file, request)
    result_file.unlink(missing_ok=True)
    try:
        worker = spawn(request_file, result_file, clone, base)
    except Exception:
        worker = None
    if not isinstance(worker, dict) or not all(worker.get(k) for k in ("pid", "host", "process_start_fingerprint")):
        job["state"] = "NEEDS_RECONCILIATION"
        atomic_json(path, job)
        # Do not release: the subprocess may have launched without a usable
        # identity, and shared model ownership must not be silently revoked.
        return {"status": "NEEDS_RECONCILIATION", "reason": "UNCONFIRMED_WORKER_LAUNCH"}
    job.update(state="RUNNING", worker=worker, attempts=int(job.get("attempts", 0)) + 1)
    atomic_json(path, job)
    return {"status": "STARTED", "id": request["id"], "pid": worker["pid"]}


def independent_verification(request: dict[str, Any], result: dict[str, Any], *,
                             run: bounded.Run = bounded.default_run) -> dict[str, Any]:
    """Supervisor re-runs the immutable Godot test on the exact authored head."""
    base, head = str(result.get("base") or ""), str(result.get("head") or "")
    workspace = Path(str(result.get("workspace") or ""))
    report: dict[str, Any] = {"passed": False, "base_sha": base, "head_sha": head, "checks": []}
    if not HEX.fullmatch(base) or not HEX.fullmatch(head) or head == base or not workspace.is_dir():
        report["reason"] = "INVALID_RESULT_IDENTITY"
        return report
    code, current = run(["git", "rev-parse", "HEAD"], workspace, 60)
    code2, diff = run(["git", "diff", "--name-only", base, head], workspace, 60)
    wanted = request["gdscript"]["module"]
    if code != 0 or code2 != 0 or current.strip() != head or diff.splitlines() != [wanted]:
        report["reason"] = "EXACT_HEAD_OR_SCOPE_MISMATCH"
        return report
    if builder.validate(request, workspace):
        report["reason"] = "GDSCRIPT_AUTHORITY_OR_TEST_CHANGED"
        return report
    try:
        task = builder.make_task(request, workspace)
    except ValueError as exc:
        report["reason"] = str(exc)[:180]
        return report
    for stage, command in (("parse", task.build), ("independent_test", task.test)):
        rc, output = run(command, workspace, 900)
        report["checks"].append({"name": stage, "ok": rc == 0, "tail": output[-500:]})
        if rc != 0:
            report["reason"] = stage.upper() + "_FAILED"
            return report
    recorded = result.get("preflight") or {}
    if not (isinstance(recorded, dict) and recorded.get("ok") is True and recorded.get("head_sha") == head):
        report["reason"] = "CANONICAL_PREFLIGHT_UNVERIFIED"
        return report
    report["checks"].append({"name": "canonical_preflight", "ok": True})
    report["passed"] = True
    return report


def publication_item(request: dict[str, Any], result: dict[str, Any], verifier: str) -> dict[str, Any]:
    """Use existing publication/CI/merge gate; never merge from this module."""
    return {
        "repository": request["repository"], "branch": result["branch"], "head_sha": result["head"],
        "state": "PUBLISH_PENDING", "verified_by": verifier, "note": "local Godot/GDScript lane",
        "title": (f"Iron God [{request['id']}]: " + request["objective"])[:120],
        "body": (f"Local Godot GDScript slice: {request['id']}\\n\\n"
                 f"{request['objective']}\\n\\n"
                 f"Base: {result['base']}\\nHead: {result['head']}\\n\\n"
                 "Godot headless parse and fixed independent behavioral test were "
                 "re-checked by the supervisor. The canonical preflight, exact-head "
                 "GitHub CI, review and existing merge gates are mandatory."),
    }


def finalize(root: Path, request: dict[str, Any], *,
             save_report: Callable[[str, dict[str, Any]], None],
             add_publication: Callable[[str, dict[str, Any]], None],
             run: bounded.Run = bounded.default_run) -> dict[str, Any]:
    job = read_job(root, request)
    if job.get("state") != "BUILT":
        return {"status": job.get("state", "NEW")}
    result = job["result"]
    verifier = "godot-" + job_path(root, request).stem
    report = independent_verification(request, result, run=run)
    save_report(verifier, report)
    if not report["passed"]:
        job["state"] = "FAILED"
        job["reason"] = report.get("reason")
        atomic_json(job_path(root, request), job)
        return {"status": "VERIFICATION_FAILED", "reason": job["reason"]}
    add_publication(verifier, publication_item(request, result, verifier))
    job["state"] = "PUBLISHED"
    atomic_json(job_path(root, request), job)
    return {"status": "PUBLICATION_REGISTERED", "id": request["id"], "verifier": verifier}
