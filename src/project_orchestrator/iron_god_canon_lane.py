"""Iron God commissioning lane: one supervisor, one worker, local-first Canon cycle.

Owner decision 2026-10-25. Each supervisor pass advances at most one packet:

PLANNED -> DISPATCHED -> IMPLEMENTING -> HANDED_BACK -> PREFLIGHT_UNVERIFIED
        -> INDEPENDENT_VERIFICATION -> ACCEPTED_LOCAL | BLOCKED

* Authority: the authoritative owner-controller decision for THIS supervisor
  process and its loaded release, re-evaluated every pass. Without it an
  active worker is stopped (its worktree and commits are preserved) and the
  packet is cancelled with evidence. Nothing here grants authority.
* Planning: the next dependency-ready slice from the versioned commissioning
  policy (engineering/iron-god-commissioning.json in the accepted release),
  with bounded allowed/forbidden paths and verification commands, and bounded
  advisory Iron Memory context.
* Isolation: a dedicated local clone of the accepted release
  (canon-workspaces/project-orchestrator) and one Git worktree per packet at
  the exact base SHA. The release directory itself is never modified.
* Verification: a worker PASS is never acceptance. The supervisor checks the
  exact handed-back head in a fresh detached worktree: changed paths, expected
  artifacts, the packet's commands and a supervisor-owned acceptance test the
  worker never saw. GREEN is recorded as ACCEPTED_LOCAL on a protected
  refs/iron-god/accepted/<packet> ref: no merge, push, PR or release.
* Memory: acceptance records a VERIFIED success_pattern lesson with exact
  commit provenance; failures stay as ledger evidence, never as lessons.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from . import iron_god_owner_control as owner_controller
from .canon_worker_memory import prepare_worker_memory_handoff, render_worker_memory_handoff
from .execution_authority import acknowledge_and_evaluate, supervisor_identity
from .learning import add_lesson, validate_memory
from .shared_memory import load_shared_memory, sync_shared_memory
from .state_io import atomic_json
from .worker_transport import (
    accept_locally,
    dispatch_canonical_slice,
    empty_ledger,
    load_ledger,
    record_independent_verification,
    record_worker_result,
    transition_packet,
    write_ledger,
)

POLICY_RELATIVE = Path("engineering") / "iron-god-commissioning.json"
TERMINAL = {"ACCEPTED_LOCAL", "BLOCKED", "CANCELLED", "PARKED", "RECONCILE_REQUIRED"}
ACTIVE = {"DISPATCHED", "IMPLEMENTING"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _git(cwd: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          check=False, timeout=timeout)


def load_policy(release_repo: Path) -> dict[str, Any] | None:
    try:
        policy = json.loads((Path(release_repo) / POLICY_RELATIVE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(policy, dict) or policy.get("mode") != "IRON_GOD_COMMISSIONING":
        return None
    return policy


class Lane:
    """Filesystem layout of the lane, rooted beside the engine's flow-state."""

    def __init__(self, state_root: Path):
        self.state_root = Path(state_root)
        self.root = self.state_root / "iron-god-canon"
        self.ledger_path = self.root / "ledger.json"
        self.active_path = self.root / "active.json"
        self.packets = self.root / "packets"
        self.handbacks = self.root / "handbacks"
        self.reports = self.root / "reports"
        self.workspaces = self.state_root.parent / "canon-workspaces"
        self.dev_repo = self.workspaces / "project-orchestrator"
        self.worktrees = self.workspaces / "worktrees"

    def ledger(self) -> dict[str, Any]:
        return load_ledger(self.ledger_path) if self.ledger_path.exists() else empty_ledger()

    def save(self, ledger: dict[str, Any], before: dict[str, Any]) -> None:
        write_ledger(self.ledger_path, ledger,
                     expected_revision=before["revision"] if self.ledger_path.exists() else None)

    def active(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.active_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def ensure_dev_repo(lane: Lane, release_repo: Path, base_sha: str) -> None:
    """A dedicated local clone of the accepted release; never the release itself."""
    if not (lane.dev_repo / ".git").exists():
        lane.workspaces.mkdir(parents=True, exist_ok=True)
        clone = subprocess.run(["git", "clone", "--quiet", "--no-local", str(release_repo), str(lane.dev_repo)],
                               capture_output=True, text=True, check=False, timeout=900)
        if clone.returncode != 0:
            raise RuntimeError("CANON_DEV_CLONE_FAILED: " + clone.stderr[-300:])
    if _git(lane.dev_repo, "cat-file", "-e", base_sha + "^{commit}").returncode != 0:
        fetch = _git(lane.dev_repo, "fetch", "--quiet", str(release_repo), base_sha, timeout=900)
        if fetch.returncode != 0:
            raise RuntimeError("CANON_BASE_FETCH_FAILED: " + fetch.stderr[-300:])


def _accepted_head(ledger: dict[str, Any], packet_id: str) -> str | None:
    record = ledger["packets"].get(packet_id)
    if record and record["state"] == "ACCEPTED_LOCAL":
        return record.get("exact_head_sha")
    return None


def next_ready_slice(policy: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any] | None:
    """First slice not yet attempted whose dependencies are all ACCEPTED_LOCAL."""
    for spec in policy.get("slices", []):
        if spec["slice_id"] in ledger["packets"]:
            continue
        if all(_accepted_head(ledger, dep) for dep in spec.get("depends_on", [])):
            return spec
    return None


def build_packet(spec: dict[str, Any], policy: dict[str, Any], *, base_sha: str, worktree: Path) -> dict[str, Any]:
    refs = spec["planning_refs"]
    branch = f"iron-god/canon/{spec['slice_id'].lower()}"
    return {
        "packet_id": spec["slice_id"],
        "project": policy["project"],
        "repository": policy["repository"],
        "source_ref": f"local-accepted:{base_sha}",
        "base_sha": base_sha,
        "branch": branch,
        "task": spec["objective"],
        "paths": sorted(spec["allowed_paths"]),
        "verification": list(spec["verification_commands"]),
        "worktree": str(worktree),
        "runner_id": policy["runner_id"],
        "planning": {
            **{key: refs[key] for key in ("vision_ref", "roadmap_ref", "architecture_ref",
                                          "current_state_ref", "dependency_evidence_ref")},
            "source_head_sha": base_sha,
            "objective": spec["objective"],
            "acceptance_criteria": list(spec["acceptance_criteria"]),
            "allowed_paths": sorted(spec["allowed_paths"]),
            "forbidden_paths": list(spec["forbidden_paths"]),
            "verification_commands": list(spec["verification_commands"]),
            "expected_artifacts": list(spec["expected_artifacts"]),
            "run_budget_minutes": int(spec["run_budget_minutes"]),
            "handoff_instructions": "Implement only the objective within allowed paths, run the "
                                    "verification commands, and hand back exact evidence.",
            "escalation_policy": "ASK_SUPERVISOR_AND_PAUSE",
        },
    }


def spawn_worker(release_repo: Path, packet_path: Path, handback_path: Path, *,
                 release_sha: str, log_path: Path) -> dict[str, Any]:
    """Start the single worker process; its identity is recorded for liveness."""
    env = {**os.environ, "PYTHONPATH": str(Path(release_repo) / "src"), "IRON_GOD_ACCEPTED_SHA": release_sha}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(release_repo) / "scripts" / "iron_god_canon_worker.py"),
             "--packet", str(packet_path), "--handback", str(handback_path)],
            cwd=str(release_repo), env=env, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=flags,
        )
    fingerprint = owner_controller.process_start_fingerprint(process.pid) or ""
    return {"pid": process.pid, "process_start_fingerprint": fingerprint,
            "host": owner_controller.socket.gethostname()}


def worker_alive(worker: dict[str, Any]) -> bool:
    try:
        return owner_controller.supervisor_alive(owner_controller.SupervisorIdentity(
            instance_id="canon-worker", pid=int(worker["pid"]),
            process_start_fingerprint=str(worker["process_start_fingerprint"]), host=str(worker["host"]),
        ))
    except (KeyError, TypeError, ValueError, owner_controller.OwnerControlError):
        return False


def stop_worker(worker: dict[str, Any]) -> None:
    if os.name == "nt" and worker_alive(worker):
        subprocess.run(["taskkill", "/PID", str(worker["pid"]), "/T", "/F"],
                       capture_output=True, text=True, check=False, timeout=60)


def independent_verification(lane: Lane, packet: dict[str, Any], spec: dict[str, Any],
                             head: str) -> tuple[bool, list[dict[str, Any]]]:
    """Re-verify the exact candidate head in a fresh detached worktree."""
    checks: list[dict[str, Any]] = []
    plan = packet["planning"]

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail[-1500:]})

    add("candidate_commit_present", _git(lane.dev_repo, "cat-file", "-e", head + "^{commit}").returncode == 0)
    ancestry = _git(lane.dev_repo, "merge-base", "--is-ancestor", packet["base_sha"], head)
    add("candidate_descends_from_exact_base", ancestry.returncode == 0)
    diff = _git(lane.dev_repo, "diff", "--name-only", packet["base_sha"], head)
    changed = sorted(p.strip() for p in diff.stdout.splitlines() if p.strip())
    allowed = set(plan["allowed_paths"])
    forbidden = [f.rstrip("/") for f in plan["forbidden_paths"]]
    outside = [p for p in changed if p not in allowed
               or any(p == f or p.startswith(f + "/") for f in forbidden)]
    add("changed_paths_within_scope", bool(changed) and not outside, ", ".join(outside or changed))
    if not all(c["ok"] for c in checks):
        return False, checks
    verify_dir = lane.workspaces / "verify" / packet["packet_id"]
    if verify_dir.exists():
        _git(lane.dev_repo, "worktree", "remove", "--force", str(verify_dir))
        shutil.rmtree(verify_dir, ignore_errors=True)
    verify_dir.parent.mkdir(parents=True, exist_ok=True)
    added = _git(lane.dev_repo, "worktree", "add", "--detach", str(verify_dir), head)
    add("fresh_verification_worktree", added.returncode == 0, added.stderr)
    if added.returncode != 0:
        return False, checks
    try:
        missing = [a for a in plan["expected_artifacts"] if not (verify_dir / a).is_file()]
        add("expected_artifacts_present", not missing, ", ".join(missing))
        env = {**os.environ, "PYTHONPATH": str(verify_dir / "src")}
        commands = list(plan["verification_commands"])
        acceptance = spec.get("supervisor_acceptance_test")
        if acceptance:
            test_path = verify_dir / "tests" / f"test_zz_supervisor_acceptance_{packet['packet_id'].lower().replace('-', '_')}.py"
            test_path.write_text(acceptance, encoding="utf-8")
            commands.append(f"python -m pytest -q -p no:cacheprovider {test_path.relative_to(verify_dir).as_posix()}")
        for command in commands:
            argv = command.split()
            if argv and argv[0] in {"python", "python.exe", "py"}:
                argv = [sys.executable, *argv[1:]]
            try:
                proc = subprocess.run(argv, cwd=verify_dir, env=env, capture_output=True, text=True,
                                      check=False, timeout=900)
                add("command: " + command, proc.returncode == 0, proc.stdout + proc.stderr)
            except (OSError, subprocess.TimeoutExpired) as exc:
                add("command: " + command, False, f"{type(exc).__name__}: {exc}")
    finally:
        _git(lane.dev_repo, "worktree", "remove", "--force", str(verify_dir))
        shutil.rmtree(verify_dir, ignore_errors=True)
    return all(c["ok"] for c in checks), checks


def record_success_memory(packet: dict[str, Any], policy: dict[str, Any], *, ref: str, head: str,
                          checks: list[dict[str, Any]], memory_root: Path | None) -> dict[str, Any]:
    lesson = {
        "project": policy.get("memory_project", "portfolio"),
        "repository": policy["repository"],
        "source_type": "success_pattern",
        "source_ref": f"{ref}@{head}",
        "symptom": packet["task"][:400],
        "root_cause": f"Iron God commissioning slice {packet['packet_id']} required this capability at base {packet['base_sha']}.",
        "fix": f"Implemented {', '.join(packet['paths'])} at {head}.",
        "verification": "Supervisor independently verified the exact head: "
                        + "; ".join(f"{c['name']}={'ok' if c['ok'] else 'FAIL'}" for c in checks)[:900],
        "regression_protection": "Committed tests plus the supervisor acceptance test for "
                                 + packet["packet_id"],
        "residual_risk": "Accepted locally only; not merged, pushed or released.",
        "evidence_state": "VERIFIED",
    }
    try:
        memory = load_shared_memory(memory_root)
    except (OSError, ValueError):
        memory = validate_memory({"schema_version": 1, "entries": []})
    return sync_shared_memory(add_lesson(memory, lesson), memory_root)


def advance_commissioning_lane(
    state_root: Path,
    release_repo: Path,
    *,
    supervisor: dict[str, Any],
    release_sha: str,
    decide: Callable[[], Any] | None = None,
    spawn: Callable[..., dict[str, Any]] = spawn_worker,
    alive: Callable[[dict[str, Any]], bool] = worker_alive,
    stop: Callable[[dict[str, Any]], None] = stop_worker,
    memory_root: Path | None = None,
) -> dict[str, Any]:
    """Advance the single-worker Canon cycle by one fail-closed step."""
    lane = Lane(state_root)
    lane.root.mkdir(parents=True, exist_ok=True)
    policy = load_policy(release_repo)
    if policy is None:
        return _report(lane, {"status": "COMMISSIONING_POLICY_UNAVAILABLE"})
    if decide is None:
        decision = acknowledge_and_evaluate(
            owner_controller.control_root(state_root), supervisor_identity(supervisor), release_sha=release_sha
        )
    else:
        decision = decide()
    ledger = lane.ledger()
    active = lane.active()
    if not getattr(decision, "permitted", False):
        reasons = list(getattr(decision, "reasons", ()) or ("EXECUTION_AUTHORITY_DENIED",))
        if active:
            stop(active["worker"])
            record = ledger["packets"].get(active["packet_id"])
            if record and record["state"] in ACTIVE:
                updated = transition_packet(ledger, active["packet_id"], state="CANCELLED",
                                            evidence_ref="owner-controller:" + ",".join(reasons)[:200])
                lane.save(updated, ledger)
            lane.active_path.unlink(missing_ok=True)
            return _report(lane, {"status": "WORKER_STOPPED_AUTHORITY_REVOKED", "packet_id": active["packet_id"],
                                  "reasons": reasons, "worktree_preserved": active.get("worktree")})
        return _report(lane, {"status": "WAITING_FOR_OWNER_AUTHORITY", "reasons": reasons})
    if active:
        record = ledger["packets"].get(active["packet_id"])
        if alive(active["worker"]):
            if record and record["state"] == "DISPATCHED":
                lane.save(transition_packet(ledger, active["packet_id"], state="IMPLEMENTING"), ledger)
            return _report(lane, {"status": "IMPLEMENTING", "packet_id": active["packet_id"],
                                  "worker_pid": active["worker"]["pid"]})
        return harvest(lane, policy, ledger, active, memory_root=memory_root)
    spec = next_ready_slice(policy, ledger)
    if spec is None:
        return _report(lane, {"status": "IDLE_NO_READY_SLICE"})
    return dispatch(lane, policy, ledger, spec, release_repo=release_repo, release_sha=release_sha,
                    spawn=spawn, memory_root=memory_root)


def dispatch(lane: Lane, policy: dict[str, Any], ledger: dict[str, Any], spec: dict[str, Any], *,
             release_repo: Path, release_sha: str, spawn: Callable[..., dict[str, Any]],
             memory_root: Path | None) -> dict[str, Any]:
    heads = [_accepted_head(ledger, dep) for dep in spec.get("depends_on", [])]
    base = heads[-1] if heads else release_sha
    ensure_dev_repo(lane, release_repo, base)
    worktree = lane.worktrees / spec["slice_id"]
    packet = build_packet(spec, policy, base_sha=base, worktree=worktree)
    if worktree.exists():
        return _report(lane, {"status": "WORKTREE_ALREADY_EXISTS_PRESERVED", "packet_id": spec["slice_id"],
                              "worktree": str(worktree)})
    added = _git(lane.dev_repo, "worktree", "add", "-b", packet["branch"], str(worktree), base)
    if added.returncode != 0:
        return _report(lane, {"status": "WORKTREE_PROVISION_FAILED", "detail": added.stderr[-300:]})
    try:
        context = prepare_worker_memory_handoff(packet, root=memory_root)
        packet["memory_context_text"] = render_worker_memory_handoff(packet, context)
        packet["memory_context_sha256"] = context["context_sha256"]
    except (ValueError, TypeError):
        packet["memory_context_text"] = "Iron Memory unavailable; use live source and repository authority only."
    updated = dispatch_canonical_slice(ledger, packet, transport="local_idle_worker")
    lane.save(updated, ledger)
    packet_path = lane.packets / f"{spec['slice_id']}.json"
    handback_path = lane.handbacks / f"{spec['slice_id']}.json"
    atomic_json(packet_path, packet)
    handback_path.unlink(missing_ok=True)
    worker = spawn(release_repo, packet_path, handback_path, release_sha=release_sha,
                   log_path=lane.reports / f"{spec['slice_id']}-worker.log")
    atomic_json(lane.active_path, {"packet_id": spec["slice_id"], "worker": worker, "worktree": str(worktree),
                                   "dispatched_at_utc": _now(), "base_sha": base})
    return _report(lane, {"status": "DISPATCHED", "packet_id": spec["slice_id"], "base_sha": base,
                          "worker_pid": worker["pid"], "worktree": str(worktree)})


def harvest(lane: Lane, policy: dict[str, Any], ledger: dict[str, Any], active: dict[str, Any], *,
            memory_root: Path | None) -> dict[str, Any]:
    packet_id = active["packet_id"]
    packet = json.loads((lane.packets / f"{packet_id}.json").read_text(encoding="utf-8"))
    spec = next(s for s in policy["slices"] if s["slice_id"] == packet_id)
    try:
        handback = json.loads((lane.handbacks / f"{packet_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        handback = {
            "project": packet["project"], "repository": packet["repository"], "branch": packet["branch"],
            "head_sha": packet["base_sha"], "probe_id": packet_id, "pattern_id": "iron-god-canon-commissioning",
            "evidence_ref": f"local://iron-god-canon/{packet_id}/missing-handback", "observed_at": _now(),
            "status": "ENVIRONMENT_FAILURE", "summary": "WORKER_EXITED_WITHOUT_HANDBACK",
        }
    current = ledger
    if current["packets"][packet_id]["state"] == "DISPATCHED":
        current = transition_packet(current, packet_id, state="IMPLEMENTING")
    current = record_worker_result(current, packet_id, handback)
    outcome: dict[str, Any] = {"packet_id": packet_id, "worker_status": handback["status"],
                               "worker_summary": handback.get("summary"), "head_sha": handback["head_sha"]}
    if handback["status"] == "PASS":
        head = handback["head_sha"]
        current = transition_packet(current, packet_id, state="PREFLIGHT_UNVERIFIED", exact_head_sha=head,
                                    evidence_ref=f"local://iron-god-canon/{packet_id}/handback")
        passed, checks = independent_verification(lane, packet, spec, head)
        atomic_json(lane.reports / f"{packet_id}-verification.json",
                    {"packet_id": packet_id, "head_sha": head, "passed": passed, "checks": checks,
                     "verified_at_utc": _now()})
        current = record_independent_verification(
            current, packet_id, verified_head_sha=head, passed=passed, checks=checks,
            evidence_ref=f"local://iron-god-canon/{packet_id}/verification",
        )
        outcome.update(verification_passed=passed,
                       failed_checks=[c["name"] for c in checks if not c["ok"]])
        if passed:
            ref = f"refs/iron-god/accepted/{packet_id.lower()}"
            created = _git(lane.dev_repo, "update-ref", ref, head, "0" * 40)
            if created.returncode != 0:
                lane.save(current, ledger)
                lane.active_path.unlink(missing_ok=True)
                return _report(lane, {**outcome, "status": "ACCEPTANCE_REF_REFUSED", "detail": created.stderr[-300:]})
            current = accept_locally(current, packet_id, accepted_ref=ref, accepted_sha=head,
                                     evidence_ref=f"local://iron-god-canon/{packet_id}/verification")
            try:
                memory = record_success_memory(packet, policy, ref=ref, head=head, checks=checks,
                                               memory_root=memory_root)
                outcome["memory"] = {"state": memory["state"], "entry_count": memory["entry_count"]}
            except (OSError, ValueError) as exc:
                outcome["memory"] = {"state": "MEMORY_WRITE_FAILED", "detail": str(exc)[:200]}
            outcome.update(accepted_ref=ref)
    lane.save(current, ledger)
    lane.active_path.unlink(missing_ok=True)
    final = current["packets"][packet_id]["state"]
    return _report(lane, {**outcome, "status": final})


def _report(lane: Lane, payload: dict[str, Any]) -> dict[str, Any]:
    payload = {"schema_version": 1, "observed_at_utc": _now(), **payload}
    atomic_json(lane.root / "lane-status.json", payload)
    return payload
