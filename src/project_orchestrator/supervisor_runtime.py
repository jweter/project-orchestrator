from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .decision_learning import compile_decision_learning
from .decision_outcome import attribute as attribute_decision_outcome
from .grant_issuance import finalize_unclaimed_grant
from .jev_evidence import load_jsonl
from .parallel_worker_pool import ParallelWorkerPool
from .recovery_manager import UNKNOWN_LIVENESS_MAX_CHECKS, process_start_fingerprint, worker_liveness
from .state_io import atomic_json

DEAD_BUILDER_STATE = "DEAD_BUILDER_STATE"


def guard_generation_iteration(state_root: Path, *, process_instance_id: str,
                               burst_id: str, actor_revision: str, sequence: int,
                               worker_pool, heartbeat_fn: Callable, handoff_fn: Callable) -> bool:
    """Publish loop progress, or revoke this exact generation before more work."""
    from .supervisor_health import generation_is_fenced
    if generation_is_fenced(state_root,process_instance_id=process_instance_id,burst_id=burst_id):
        atomic_json(state_root/"supervisor-generation-handoff.json",{
            "schema_version":1,"status":"FENCED_GENERATION_EXITING",
            "process_instance_id":process_instance_id,"burst_id":burst_id,
            "observed_at_utc":datetime.now(UTC).isoformat(),
        })
        return True
    atomic_json(state_root/"supervisor-progress.json",{
        "schema_version":1,"status":"LOOP_STARTED","sequence":sequence,
        "process_instance_id":process_instance_id,"burst_id":burst_id,
        "actor_revision":actor_revision,"observed_at_utc":datetime.now(UTC).isoformat(),
    })
    heartbeat_fn(state_root,process_instance_id=process_instance_id,burst_id=burst_id,actor_revision=actor_revision)
    return handoff_fn(state_root,process_instance_id=process_instance_id,worker_pool=worker_pool)=="DRAINING_FOR_TAKEOVER"


def builder_health(
    state_root: Path,
    returncode: int,
    expected_invocation_id: str | None = None,
) -> tuple[bool, str]:
    path = state_root / "builder.json"
    if returncode != 0:
        return False, "PROCESS_FAILURE"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        status = payload.get("status", "UNKNOWN")
    except (OSError, json.JSONDecodeError):
        return False, "STATE_UNREADABLE"
    if expected_invocation_id is not None and payload.get("invocation_id") != expected_invocation_id:
        return False, "STALE_BUILDER_STATE"
    degraded = {
        "P1_BUILDER_FAILURE", "BUILDER_FAILED", "PREFLIGHT_FAILED",
        "PUSH_FAILED", "PR_CREATE_FAILED",
    }
    productive = {
        "USER_ACTIVE", "IMPLEMENTING", "PREFLIGHT_GREEN", "PR_OPEN",
        "CI_PENDING", "GREEN", "MERGED", "PRODUCT_REALITY_PENDING",
        "PRODUCT_REALITY_VERIFIED",
    }
    return status in productive and status not in degraded, status


def refresh_decision_learning(state_root: Path) -> dict:
    ledger = state_root / "telemetry" / "jev-shadow" / "decisions.jsonl"
    records = load_jsonl(ledger)
    try:
        lifecycle = json.loads(
            (state_root / "hands-off-grant-lifecycle.json").read_text(encoding="utf-8")
        ).get("grants", {})
    except (OSError, json.JSONDecodeError, TypeError):
        lifecycle = {}
    rows = []
    for record in records:
        for grant_id, result in lifecycle.items():
            if not isinstance(result, dict):
                continue
            enriched = dict(result)
            enriched.setdefault(
                "candidate_id",
                result.get("lane_id") or result.get("slice_id") or grant_id,
            )
            row = attribute_decision_outcome(record, enriched)
            if row:
                rows.append(row)
    learning = compile_decision_learning(rows)
    payload = {
        "schema_version": 1,
        "attributed_outcomes": len(rows),
        "learning": learning,
        "updated_at_utc": datetime.now(UTC).isoformat(),
    }
    atomic_json(state_root / "decision-learning.json", payload)
    return payload


def _acquire_os_file_lock(handle) -> bool:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, BlockingIOError):
        return False


def _release_os_file_lock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _acquire_supervisor_lock_record(
    path: Path,
    metadata: Path,
    *,
    lock_id: str,
    worker: dict,
    extra_metadata: dict | None = None,
) -> dict | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b", buffering=0)
    if not _acquire_os_file_lock(handle):
        handle.close()
        return None
    payload = {
        "schema_version": 1,
        "lock_id": lock_id,
        "worker": worker,
        "acquired_at_utc": datetime.now(UTC).isoformat(),
    }
    if extra_metadata:
        payload.update(extra_metadata)
    try:
        atomic_json(metadata, payload)
    except OSError:
        try:
            _release_os_file_lock(handle)
        finally:
            handle.close()
        raise
    return {
        "path": path,
        "metadata_path": metadata,
        "handle": handle,
        "lock_id": lock_id,
        "worker": worker,
    }


def _release_supervisor_lock_record(lock: dict) -> None:
    handle = lock["handle"]
    metadata = lock["metadata_path"]
    try:
        if metadata.exists():
            try:
                current = json.loads(metadata.read_text(encoding="utf-8"))
                if current.get("lock_id") == lock.get("lock_id"):
                    metadata.unlink()
            except (OSError, json.JSONDecodeError, TypeError):
                pass
    finally:
        try:
            _release_os_file_lock(handle)
        finally:
            handle.close()


def acquire_supervisor_instance_lock(
    state_root: Path,
    *,
    machine_lock_root: Path | None = None,
    fingerprint_fn: Callable[[int | None], str | None] = process_start_fingerprint,
) -> dict | None:
    state_root.mkdir(parents=True, exist_ok=True)
    lock_id = str(uuid.uuid4())
    worker = {
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "process_start_fingerprint": fingerprint_fn(None),
        "instance_id": uuid.uuid4().hex,
    }

    machine_lock = None
    if machine_lock_root is not None:
        machine_lock_root.mkdir(parents=True, exist_ok=True)
        machine_lock = _acquire_supervisor_lock_record(
            machine_lock_root / "continuous-flow-supervisor.lock",
            machine_lock_root / "continuous-flow-supervisor.lock.json",
            lock_id=lock_id,
            worker=worker,
            extra_metadata={"state_root": str(state_root.resolve())},
        )
        if machine_lock is None:
            return None

    local_lock = _acquire_supervisor_lock_record(
        state_root / "supervisor-instance.lock",
        state_root / "supervisor-instance.lock.json",
        lock_id=lock_id,
        worker=worker,
    )
    if local_lock is None:
        if machine_lock is not None:
            _release_supervisor_lock_record(machine_lock)
        return None
    local_lock["machine_lock"] = machine_lock
    return local_lock


def release_supervisor_instance_lock(lock: dict) -> None:
    machine_lock = lock.get("machine_lock")
    try:
        _release_supervisor_lock_record(lock)
    finally:
        if machine_lock is not None:
            _release_supervisor_lock_record(machine_lock)


def write_execution_grant(
    repo_root: Path,
    state_root: Path,
    products: list[str],
    planned_target: tuple[str, str, dict] | None = None,
    *,
    acquire_issue_lock_fn: Callable,
    release_issue_lock_fn: Callable,
    recover_stale_issued_fn: Callable,
    select_target_fn: Callable,
    load_slice_fn: Callable,
    planner_id_fn: Callable,
    conflict_fn: Callable,
    select_runner_fn: Callable,
    runtime_for_task_fn: Callable,
    reserve_grant_fn: Callable,
    paths_conflict_fn: Callable,
) -> Path:
    lock = acquire_issue_lock_fn(state_root)
    try:
        activation_path = state_root / "hands-off-activation.json"
        activation_bytes = activation_path.read_bytes()
        activation = json.loads(activation_bytes)
        contract_path = repo_root / "engineering" / "hands-off-evolution.json"
        contract_bytes = contract_path.read_bytes()

        recover_stale_issued_fn(state_root)

        if planned_target is None:
            repo, exact_head = select_target_fn(state_root, activation, products)
            execution_slice = load_slice_fn(state_root, repo, exact_head)
        else:
            repo, exact_head, execution_slice = planned_target
            if execution_slice.get("repository") != repo or execution_slice.get("exact_head") != exact_head:
                raise RuntimeError("PLANNED_SLICE_IDENTITY_MISMATCH")
            if not any(
                repo in activation["products"][name]["repositories"]
                and activation["products"][name]["repositories"][repo] == exact_head
                for name in products
                if name in activation.get("products", {})
            ):
                raise RuntimeError("PLANNED_SLICE_HEAD_STALE")

        selected_products = {
            name
            for name in products
            if repo in activation["products"][name]["repositories"]
        }

        planner_id = None
        if planned_target is not None and len(selected_products) == 1:
            planner_id = planner_id_fn(
                next(iter(selected_products)),
                repo,
                exact_head,
                execution_slice,
            )

        conflict = conflict_fn(state_root, repo, execution_slice)
        if conflict:
            raise RuntimeError(
                f"EXECUTION_SLICE_ALREADY_OWNED:{conflict['grant_id']}:{conflict['state']}"
            )

        grant_id = str(uuid.uuid4())
        runner_id = execution_slice.get("runner_id") or select_runner_fn(execution_slice)
        runtime_id = execution_slice.get("execution_runtime") or runtime_for_task_fn(execution_slice)
        execution_slice = {
            **execution_slice,
            "runner_id": runner_id,
            "execution_runtime": runtime_id,
        }

        grant = {
            "schema_version": 5,
            "grant_id": grant_id,
            "worker_id": execution_slice.get("worker_id"),
            "contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
            "activation_sha256": hashlib.sha256(activation_bytes).hexdigest(),
            "products": {
                name: {"repositories": activation["products"][name]["repositories"]}
                for name in selected_products
            },
            "execution_slice": execution_slice,
        }

        return reserve_grant_fn(
            state_root,
            grant,
            repo,
            exact_head,
            execution_slice,
            runner_id,
            runtime_id,
            planner_id,
            lock["worker"],
            paths_conflict_fn,
        )
    finally:
        release_issue_lock_fn(lock)


def mark_builder_running(
    builder_state: Path,
    builder_output: Path,
    *,
    invocation_id: str,
    grant_path: Path,
    worker_id: str | None,
    runner_id: str,
    runtime_id: str,
    fingerprint_fn: Callable[[int | None], str | None] = process_start_fingerprint,
) -> dict:
    try:
        grant_id = json.loads(grant_path.read_text(encoding="utf-8")).get("grant_id")
    except (OSError, json.JSONDecodeError, TypeError):
        grant_id = None
    payload = {
        "schema_version": 1,
        "status": "BUILDER_RUNNING",
        "invocation_id": invocation_id,
        "worker_id": worker_id,
        "runner_id": runner_id,
        "runtime_id": runtime_id,
        "started_at_utc": datetime.now(UTC).isoformat(),
        # Binds this BUILDER_RUNNING record to the concrete process/incarnation
        # holding it, so a future cycle can prove the record dead rather than
        # trusting a status string a crashed process can never update again.
        "worker": {
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "process_start_fingerprint": fingerprint_fn(None),
        },
    }
    if grant_id:
        payload["grant_id"] = grant_id
    atomic_json(builder_state, payload)
    builder_output.write_text("", encoding="utf-8")
    return payload


def reconcile_dead_builder_states(
    state_root: Path,
    now: datetime | None = None,
    *,
    liveness_probe: Callable[[dict | None], str] = worker_liveness,
    unknown_liveness_max_checks: int = UNKNOWN_LIVENESS_MAX_CHECKS,
) -> dict[str, str]:
    """Terminal-state BUILDER_RUNNING records whose owning process is proven dead.

    A crashed or replaced supervisor/worker incarnation can leave a
    worker-state builder.json permanently claiming BUILDER_RUNNING. Nothing
    can ever update that file again, so it must never be trusted as evidence
    that a worker slot is occupied. This scans every worker-state builder.json
    (plus the legacy top-level file) and, only once the recorded owning
    process is provably dead, rewrites the record to a terminal
    DEAD_BUILDER_STATE with a structured reason. Liveness that cannot be
    proven (no recorded identity, cross-host, PID reuse ambiguity) fails
    closed and is retried on a later cycle, quarantining only after repeated
    unknown checks, mirroring grant-claim liveness reconciliation.
    """
    current = now or datetime.now(UTC)
    outcomes: dict[str, str] = {}
    candidates: list[Path] = []
    top_level = state_root / "builder.json"
    if top_level.exists():
        candidates.append(top_level)
    worker_state_dir = state_root / "worker-state"
    if worker_state_dir.is_dir():
        candidates.extend(sorted(worker_state_dir.glob("*/builder.json")))

    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict) or payload.get("status") != "BUILDER_RUNNING":
            continue
        try:
            key = str(path.relative_to(state_root))
        except ValueError:
            key = str(path)

        liveness = liveness_probe(payload.get("worker"))
        if liveness == "DEAD":
            payload["status"] = DEAD_BUILDER_STATE
            payload["prior_status"] = "BUILDER_RUNNING"
            payload["dead_builder_reason"] = DEAD_BUILDER_STATE
            payload["dead_builder_reconciled_at_utc"] = current.isoformat()
            payload.pop("dead_builder_unknown_checks", None)
            atomic_json(path, payload)
            outcomes[key] = DEAD_BUILDER_STATE
        elif liveness == "ALIVE":
            if payload.pop("dead_builder_unknown_checks", None) is not None:
                atomic_json(path, payload)
            outcomes[key] = "BUILDER_RUNNING_OWNER_ALIVE"
        else:
            checks = int(payload.get("dead_builder_unknown_checks", 0)) + 1
            payload["dead_builder_unknown_checks"] = checks
            payload["dead_builder_last_liveness_check_at_utc"] = current.isoformat()
            if checks >= unknown_liveness_max_checks:
                payload["status"] = DEAD_BUILDER_STATE
                payload["prior_status"] = "BUILDER_RUNNING"
                payload["dead_builder_reason"] = f"{DEAD_BUILDER_STATE}_UNKNOWN_QUARANTINED"
                payload["dead_builder_reconciled_at_utc"] = current.isoformat()
                outcomes[key] = f"{DEAD_BUILDER_STATE}_UNKNOWN_QUARANTINED"
            else:
                outcomes[key] = "BUILDER_RUNNING_OWNER_UNKNOWN"
            atomic_json(path, payload)
    return outcomes


def run_builder(
    repo_root: Path,
    workspace_root: Path,
    state_root: Path,
    products: list[str] | None = None,
    worker_id: str | None = None,
    planned_target: tuple[str, str, dict] | None = None,
    *,
    write_grant_fn: Callable,
    runtime_lease_state: Path,
    finalize_fn: Callable = finalize_unclaimed_grant,
    run_fn: Callable = subprocess.run,
    sleep_fn: Callable[[float], None] = time.sleep,
    mark_running_fn: Callable = mark_builder_running,
) -> tuple[int, str | None]:
    worker_dir = state_root / "worker-state" / worker_id if worker_id else state_root
    worker_dir.mkdir(parents=True, exist_ok=True)
    builder_state = worker_dir / "builder.json"
    builder_output = worker_dir / "builder-last-output.txt"

    if not products:
        builder_output.write_text("EXECUTION_GRANT_REQUIRED\n", encoding="utf-8")
        return 2, None

    try:
        grant_path = None
        grant_issue_backoff=(0.05,0.10,0.20,0.30,0.40,0.50,0.50)
        for attempt,backoff in enumerate(grant_issue_backoff):
            try:
                grant_path = write_grant_fn(
                    repo_root,
                    state_root,
                    products,
                    planned_target=planned_target,
                )
                break
            except RuntimeError as exc:
                if str(exc) != "GRANT_ISSUANCE_BUSY":
                    raise
                if attempt == len(grant_issue_backoff)-1:
                    atomic_json(
                        builder_state,
                        {"status": "OWNERSHIP_HELD", "reason": str(exc)},
                    )
                    return 0, None
                sleep_fn(backoff)
        assert grant_path is not None
    except RuntimeError as exc:
        if str(exc) == "ALL_EXECUTION_LANES_COMPLETE":
            atomic_json(
                builder_state,
                {"status": "NO_WORK", "reason": "ALL_EXECUTION_LANES_COMPLETE"},
            )
            builder_output.write_text(
                "ALL_EXECUTION_LANES_COMPLETE\n",
                encoding="utf-8",
            )
            return 0, None
        if str(exc).startswith("EXECUTION_SLICE_ALREADY_OWNED:"):
            atomic_json(
                builder_state,
                {"status": "OWNERSHIP_HELD", "reason": str(exc)},
            )
            builder_output.write_text(str(exc) + "\n", encoding="utf-8")
            return 0, None
        raise

    invocation_id = uuid.uuid4().hex
    grant_payload = json.loads(grant_path.read_text(encoding="utf-8"))
    grant_authority = grant_payload.get("execution_slice", {})
    decision_path = state_root / "dispatch-decision.json"
    try:
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        decision = {}
    if not isinstance(decision, dict):
        decision = {}
    selected_product = next(iter(grant_payload.get("products", {})), None)
    selected_repository = grant_authority.get("repository")
    decision.update({
        "schema_version": 1,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "invocation_id": invocation_id,
        "grant_path": str(grant_path),
        "selected_product": selected_product,
        "selected_repository": selected_repository,
        "decisions": [{
            "outcome": "DISPATCHED",
            "product": selected_product,
            "repository": selected_repository,
            "head_sha": grant_authority.get("exact_head"),
            "reason": "EXECUTION_GRANT_ISSUED",
        }],
    })
    atomic_json(decision_path, decision)

    runner_id = grant_authority.get("runner_id")
    runtime_id = grant_authority.get("execution_runtime")
    runtime_lease_id = grant_authority.get("runtime_lease_id")
    resource_lease_ids = grant_authority.get("resource_lease_ids", {})

    if not isinstance(resource_lease_ids, dict):
        raise RuntimeError("RESOURCE_LEASE_AUTHORITY_INVALID")
    if not runner_id or not runtime_id:
        raise RuntimeError("EXECUTION_GRANT_IDENTITY_REQUIRED")
    if not runtime_lease_id:
        raise RuntimeError("RUNTIME_LEASE_ID_REQUIRED")

    cmd = [
        sys.executable,
        str(repo_root / "scripts" / "autonomous_slice_builder.py"),
        "--workspace-root",
        str(workspace_root),
        "--state",
        str(builder_state),
        "--execution-grant",
        str(grant_path),
        "--invocation-id",
        invocation_id,
        "--runner-id",
        runner_id,
        "--runtime-id",
        runtime_id,
        "--runtime-lease-id",
        runtime_lease_id,
        "--runtime-lease-state",
        str(runtime_lease_state),
        "--resource-lease-ids",
        json.dumps(resource_lease_ids, sort_keys=True),
    ]

    mark_running_fn(
        builder_state,
        builder_output,
        invocation_id=invocation_id,
        grant_path=grant_path,
        worker_id=worker_id,
        runner_id=runner_id,
        runtime_id=runtime_id,
    )

    process = run_fn(
        cmd,
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    builder_output.write_text(
        (process.stdout or "") + (process.stderr or ""),
        encoding="utf-8",
    )
    finalize_fn(grant_path, process.returncode, builder_state)
    return process.returncode, invocation_id


def run_builder_for_product(
    repo_root: Path,
    workspace_root: Path,
    state_root: Path,
    product: str,
    worker_id: str | None = None,
    planned_target: tuple[str, str, dict] | None = None,
    *,
    run_builder_fn: Callable,
) -> tuple[int, str | None]:
    return run_builder_fn(
        repo_root,
        workspace_root,
        state_root,
        [product],
        worker_id=worker_id,
        planned_target=planned_target,
    )


def harvest_parallel_builders(
    pool: ParallelWorkerPool,
    state_root: Path,
    *,
    health_fn: Callable = builder_health,
) -> dict:
    completed = pool.harvest()
    rows = {}
    for worker_id, result in completed.items():
        if isinstance(result, BaseException):
            rows[worker_id] = {
                "status": "WORKER_EXCEPTION",
                "error_class": type(result).__name__,
            }
        else:
            returncode, invocation_id = result
            worker_state = state_root / "worker-state" / worker_id
            ok, status = health_fn(worker_state, returncode, invocation_id)
            repository = None
            try:
                payload = json.loads((worker_state / "builder.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                payload = {}
            if (
                invocation_id is not None
                and isinstance(payload, dict)
                and payload.get("invocation_id") == invocation_id
            ):
                candidate = payload.get("repository")
                if isinstance(candidate, str) and candidate.strip():
                    repository = candidate.strip()
            rows[worker_id] = {
                "status": status,
                "returncode": returncode,
                "healthy": ok,
                "invocation_id": invocation_id,
                "repository": repository,
            }
    if rows:
        atomic_json(
            state_root / "parallel-worker-results.json",
            {
                "schema_version": 1,
                "completed": rows,
                "recorded_at_utc": datetime.now(UTC).isoformat(),
            },
        )
    return rows


def reconcile_runtime_leases(runtime_leases, pool: ParallelWorkerPool) -> list[str]:
    """Reclaim dead-owner leases plus current-generation orphaned worker leases."""
    recovered = list(runtime_leases.recover_stale())
    snapshot = pool.snapshot()
    active_worker_ids = {
        row["worker_id"]
        for row in snapshot.get("workers", [])
        if isinstance(row, dict) and isinstance(row.get("worker_id"), str) and row["worker_id"]
    }
    recovered.extend(runtime_leases.reconcile_owned_workers(active_worker_ids))
    return sorted(set(recovered))


def prove_restart_takeover(state_root: Path, instance_lock: dict, burst: dict) -> dict:
    current = instance_lock["worker"]
    previous_id = burst.get("previous_process_instance_id")
    proof = {
        "schema_version": 1,
        "burst_id": burst["burst_id"],
        "process_instance_id": current["instance_id"],
        "previous_process_instance_id": previous_id,
        "resumed": bool(burst.get("resumed")),
        "proven_at_utc": datetime.now(UTC).isoformat(),
    }
    if not burst.get("resumed"):
        proof["status"] = "NEW_BURST_NO_TAKEOVER"
        return proof
    if not previous_id:
        proof["status"] = "RESTART_PROVEN_BY_EXCLUSIVE_KERNEL_LOCK"
        proof["previous_owner_evidence"] = "LEGACY_IDENTITY_UNAVAILABLE"
        return proof
    if previous_id == current["instance_id"]:
        proof["status"] = "RESTART_IDENTITY_COLLISION"
        return proof
    proof["status"] = "RESTART_PROVEN_BY_EXCLUSIVE_KERNEL_LOCK"
    proof["previous_owner_evidence"] = "KERNEL_LOCK_RELEASED_OR_PROCESS_EXITED"
    return proof


def start_or_resume_burst(
    state_root: Path,
    *,
    process_instance_id: str | None = None,
    now: datetime | None = None,
    resume_seconds: int = 900,
) -> dict:
    current = now or datetime.now(UTC)
    path = state_root / "continuation-current-burst.json"
    try:
        prior = json.loads(path.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(
            str(prior.get("updated_at_utc", "")).replace("Z", "+00:00")
        )
        age = (current - updated).total_seconds()
        schema = prior.get("schema_version")
        burst_id = prior.get("burst_id")
        pass_count = prior.get("pass_count")
        prior_instance = prior.get("process_instance_id")
        valid_schema = schema in {1, 2}
        valid_burst = isinstance(burst_id, str) and 1 <= len(burst_id) <= 128
        valid_pass = (
            isinstance(pass_count, int)
            and not isinstance(pass_count, bool)
            and 0 <= pass_count <= 1_000_000
        )
        valid_instance = (
            schema == 1
            or (
                isinstance(prior_instance, str)
                and 1 <= len(prior_instance) <= 128
            )
        )
        if (
            prior.get("immediate_continuation") is True
            and valid_schema
            and valid_burst
            and valid_pass
            and valid_instance
            and 0 <= age <= resume_seconds
        ):
            return {
                "burst_id": burst_id,
                "burst_pass": pass_count,
                "resumed": True,
                "resume_age_seconds": age,
                "previous_process_instance_id": prior_instance,
                "process_instance_id": process_instance_id,
            }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass

    return {
        "burst_id": current.strftime("%Y%m%dT%H%M%S.%fZ"),
        "burst_pass": 0,
        "resumed": False,
        "resume_age_seconds": None,
        "previous_process_instance_id": None,
        "process_instance_id": process_instance_id,
    }
