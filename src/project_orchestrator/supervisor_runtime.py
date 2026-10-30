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
from typing import Any, Callable

from .decision_learning import compile_decision_learning
from .decision_outcome import attribute as attribute_decision_outcome
from . import iron_god_owner_control as _owner_controller
from .execution_authority import acknowledge_and_evaluate, supervisor_identity
from .human_ownership import AUTHORIZED, OWNER_CONTROL_ACTIVE, owner_control_status
from .grant_issuance import finalize_unclaimed_grant
from .grant_lifecycle import recovery_lifecycle_write_budget
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
        "USER_ACTIVE", "IMPLEMENTING", "PREFLIGHT_GREEN", "PUSH_PENDING",
        "PR_OPEN_PENDING", "PR_OPEN", "CI_PENDING", "GREEN", "MERGED",
        "PRODUCT_REALITY_PENDING", "PRODUCT_REALITY_VERIFIED",
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


def bind_builder_worker_identity(state_path: Path, invocation_id: str, worker: dict) -> bool:
    """Transfer starting-state ownership from the supervisor to the concrete child."""
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "BUILDER_STARTING"
        or payload.get("invocation_id") != invocation_id
    ):
        return False
    payload["status"] = "BUILDER_RUNNING"
    payload["worker"] = worker
    payload["worker_bound_at_utc"] = datetime.now(UTC).isoformat()
    atomic_json(state_path, payload)
    return True


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
        "status": "BUILDER_STARTING",
        "invocation_id": invocation_id,
        "worker_id": worker_id,
        "runner_id": runner_id,
        "runtime_id": runtime_id,
        "started_at_utc": datetime.now(UTC).isoformat(),
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


def worker_exception_diagnostic(error: BaseException) -> dict[str, str]:
    """Return actionable, allowlisted evidence without leaking exception text.

    Runner failures may contain credentials, local paths or private task
    context. Only static reason codes and known source locations enter shared
    telemetry. Classification never grants an execution retry.
    """
    message = str(error).strip()
    lowered = message.lower()
    denial_fragments = (
        "blocked by openai's safety checks",
        "blocked by safety checks",
        "[security weaken]",
        "[create unsafe agents]",
        "tool call was blocked",
    )
    known_authority_codes = {
        "EXECUTION_GRANT_REQUIRED",
        "EXECUTION_GRANT_IDENTITY_REQUIRED",
        "CANDIDATE_EXECUTION_NOT_AUTHORIZED",
        "OWNER_CONTROL_UNVERIFIED",
    }
    known_precondition_codes = {
        "PLANNED_SLICE_IDENTITY_MISMATCH",
        "PLANNED_SLICE_HEAD_STALE",
        "NO_READY_EXECUTION_SLICE",
        "GRANT_ISSUANCE_BUSY",
    }
    if any(fragment in lowered for fragment in denial_fragments):
        category, code = "PLATFORM_DENIAL", "PLATFORM_SAFETY_DENIED"
    elif message in known_authority_codes:
        category, code = "AUTHORIZATION_DENIED", message
    elif message.startswith("EXECUTION_SLICE_ALREADY_OWNED:"):
        category, code = "OWNERSHIP_BLOCKED", "EXECUTION_SLICE_ALREADY_OWNED"
    elif message in known_precondition_codes:
        category, code = "PRECONDITION_FAILED", message
    elif isinstance(error, (PermissionError, FileNotFoundError, TimeoutError, OSError)):
        category, code = "ENVIRONMENT_FAILURE", type(error).__name__
    elif isinstance(error, (AssertionError, ValueError, TypeError)):
        category, code = "CODE_FAILURE", type(error).__name__
    else:
        category, code = "UNCLASSIFIED", "UNCLASSIFIED_EXCEPTION"

    result = {"failure_class": category, "failure_code": code}
    allowed_sites = {
        "supervisor_runtime.py",
        "continuous_flow_supervisor.py",
        "grant_issuance.py",
        "laptop_actor_runner.py",
        "autonomous_slice_builder.py",
        "parallel_worker_pool.py",
    }
    tb = error.__traceback__
    while tb is not None:
        basename = Path(tb.tb_frame.f_code.co_filename).name
        if basename in allowed_sites and 0 < tb.tb_lineno < 1000000:
            result["failure_site"] = f"{basename}:{tb.tb_lineno}"
        tb = tb.tb_next
    return result


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
                "error_code": (
                    str(result) if len(str(result)) <= 100
                    and str(result).isascii() and str(result).isidentifier()
                    and str(result).isupper() else "UNCLASSIFIED"
                ),
                **worker_exception_diagnostic(result),
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


def run_lifecycle_recovery_pass(
    state_root: Path, workspace_root: Path, *,
    recover_generations_fn: Callable, recover_claims_fn: Callable,
    pending_fn: Callable, preserved_fn: Callable, quarantined_fn: Callable,
    terminalize_fn: Callable | None = None,
) -> dict:
    """Defer persistent Windows lifecycle contention without losing atomic authority."""
    with recovery_lifecycle_write_budget():
        try:
            abandoned = recover_claims_fn(state_root)
            return {
                "publication_blocked": False,
                "released": recover_generations_fn(state_root),
                "abandoned": abandoned,
                "queue": pending_fn(state_root, abandoned),
                "preserved": preserved_fn(workspace_root, state_root),
                "quarantine_rechecks": quarantined_fn(state_root),
                "terminalized_quarantines": terminalize_fn(state_root) if terminalize_fn else {},
            }
        except PermissionError:
            return {
                "publication_blocked": True, "released": [], "abandoned": [],
                "queue": [], "preserved": {}, "quarantine_rechecks": {}, "terminalized_quarantines": {},
            }


def prepare_actor_revision_rollover(
    repo_root: Path, workspace_root: Path, state_root: Path, *, loaded_actor_revision: str | None,
    current_actor_revision: str | None, process_instance_id: str, worker_pool,
    builder_seconds: int, merge_seconds: int, yield_on_interactive_session: bool = False,
    popen_fn: Callable = subprocess.Popen, fingerprint_fn: Callable = process_start_fingerprint,
) -> dict:
    """Launch an authorized successor before asking the current generation to drain."""
    if not loaded_actor_revision or not current_actor_revision or current_actor_revision == loaded_actor_revision:
        return {"requested": False, "status": "REVISION_CURRENT"}
    receipt_path = state_root / "supervisor-revision-successor.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        receipt = {}
    successor_live = False
    try:
        pid = int(receipt.get("pid"))
        expected = receipt.get("process_start_fingerprint")
        successor_live = (
            receipt.get("predecessor_process_instance_id") == process_instance_id
            and bool(expected) and fingerprint_fn(pid) == expected
        )
    except (TypeError, ValueError):
        successor_live = False
    if not successor_live:
        cmd = [
            sys.executable, str(repo_root / "scripts" / "launch_persistent_supervisor.py"),
            "--repo-root", str(repo_root), "--workspace-root", str(workspace_root),
            "--state-root", str(state_root), "--builder-seconds", str(builder_seconds),
            "--merge-seconds", str(merge_seconds), "--wait-for-takeover",
        ]
        if yield_on_interactive_session:
            cmd.append("--yield-on-interactive-session")
        flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                 | getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo_root / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        try:
            child = popen_fn(cmd, cwd=repo_root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True, env=env)
        except OSError:
            return {"requested": False, "status": "SUCCESSOR_LAUNCH_FAILED"}
        fingerprint = fingerprint_fn(child.pid)
        if not fingerprint:
            try:
                child.terminate()
            except (AttributeError, OSError):
                pass
            return {"requested": False, "status": "SUCCESSOR_IDENTITY_UNPROVEN"}
        atomic_json(receipt_path, {
            "schema_version": 1, "status": "SUCCESSOR_LAUNCHER_ALIVE", "pid": child.pid,
            "process_start_fingerprint": fingerprint,
            "predecessor_process_instance_id": process_instance_id,
            "loaded_actor_revision": loaded_actor_revision, "current_actor_revision": current_actor_revision,
            "launched_at_utc": datetime.now(UTC).isoformat(),
        })
    active_workers = worker_pool.snapshot()["running_total"]
    atomic_json(state_root / "supervisor-takeover-request.json", {
        "schema_version": 1, "status": "TAKEOVER_REQUESTED",
        "predecessor_process_instance_id": process_instance_id, "reason": "ACTOR_REVISION_ADVANCED",
        "loaded_actor_revision": loaded_actor_revision, "current_actor_revision": current_actor_revision,
        "requested_at_utc": datetime.now(UTC).isoformat(),
    })
    return {"requested": True, "status": "WAITING_FOR_WORKERS" if active_workers else "DRAINING_FOR_TAKEOVER",
            "active_workers": active_workers}


# Owner directive (2026-10-25): commission one supervisor with ONE worker and
# prove a complete authorized single-worker cycle before any parallelism.
# Raising these limits requires that verified cycle; the previous runtime
# capacity was max_workers_per_product=2, max_total_workers=6.
COMMISSIONING_WORKER_LIMITS = {"max_workers_per_product": 1, "max_total_workers": 1}

def authorized_dispatch_products(
    state_root: Path,
    products: list[str],
    decide: Callable[[], object] | None = None,
    *,
    supervisor: dict[str, Any] | None = None,
    release_sha: str | None = None,
) -> list[str]:
    """Return dispatchable products only when the single execution decision permits.

    Activation readiness says which products *could* advance; it is not
    authority. The owner controller decision is re-evaluated every pass for
    this exact supervisor process and release, so a revocation stops new
    dispatch at the next iteration. Harvest and recovery of already-running
    work are unaffected. The decision is recorded for the owner and the
    desktop proof; a failing or malformed decision denies.
    """
    try:
        if decide is None:
            if supervisor is None:
                raise ValueError("supervisor identity required")
            identity = supervisor_identity(supervisor)
            decision = acknowledge_and_evaluate(
                _owner_controller.control_root(state_root), identity, release_sha=release_sha
            )
        else:
            decision = decide()
        permitted = getattr(decision, "permitted", False) is True
        reasons = [str(r) for r in (getattr(decision, "reasons", ()) or ())]
    except Exception:
        permitted, reasons = False, ["EXECUTION_AUTHORITY_UNVERIFIED"]
    if not permitted and not reasons:
        reasons = ["EXECUTION_AUTHORITY_DENIED"]
    atomic_json(state_root / "supervisor-execution-authority.json", {
        "schema_version": 1,
        "permitted": permitted,
        "reasons": reasons,
        "candidate_products": list(products),
        "supervisor_instance_id": (supervisor or {}).get("instance_id"),
        "release_sha": release_sha,
        "observed_at_utc": datetime.now(UTC).isoformat(),
    })
    return list(products) if permitted else []


def owner_reclaimed(
    state_root: Path,
    supervisor: dict[str, Any],
    release_sha: str | None,
    *,
    process_instance_id: str,
) -> bool:
    """Loop-top owner check: acknowledge a fresh grant, or stop and ack revocation.

    A pending Take the Wheel is acknowledged by this exact supervisor before
    any slow work, so the acknowledgment window cannot lapse during a pass.
    Returns True when owner control is not granted; the caller must stop
    dispatch and exit. A Give Me Control is acknowledged here, after which no
    further dispatch can occur in this process.
    """
    root = _owner_controller.control_root(state_root)
    try:
        acknowledge_and_evaluate(root, supervisor_identity(supervisor), release_sha=release_sha)
    except Exception:
        pass  # The owner status below fails closed on any controller problem.
    status = owner_control_status(state_root)
    if status == AUTHORIZED:
        return False
    revocation_acknowledged = False
    if status == OWNER_CONTROL_ACTIVE:
        try:
            revocation_acknowledged = _owner_controller.acknowledge_revocation(
                root, supervisor_identity(supervisor)
            )
        except Exception:
            revocation_acknowledged = False
    atomic_json(state_root / "supervisor-human-ownership.json", {
        "schema_version": 2,
        "status": "HUMAN_MODE_ACTIVE",
        "owner_status": status,
        "revocation_acknowledged": revocation_acknowledged,
        "process_instance_id": process_instance_id,
        "observed_at_utc": datetime.now(UTC).isoformat(),
    })
    return True


def wait_for_completion_or_reclaim(
    worker_pool: Any,
    delay_seconds: float,
    state_root: Path,
    *,
    poll_seconds: float = 2.0,
    owner_status_fn: Callable[[Path], str] = owner_control_status,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Pace the next pass, waking early on worker completion or owner reclaim.

    The single pre-existing wait could hold a Give Me Control for a whole
    builder interval. Polling the owner controller in short slices lets the
    loop-top gate acknowledge the revocation promptly. Returns COMPLETED,
    OWNER_RECLAIMED or TIMEOUT.
    """
    deadline = clock() + max(0.0, float(delay_seconds))
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return "TIMEOUT"
        if worker_pool.wait_for_completion(min(poll_seconds, remaining)):
            return "COMPLETED"
        if owner_status_fn(state_root) != AUTHORIZED:
            return "OWNER_RECLAIMED"


def commissioning_products(
    state_root: Path,
    repo_root: Path,
    products: list[str],
    *,
    supervisor: dict[str, Any],
    release_sha: str | None,
    advance: Callable[..., dict[str, Any]] | None = None,
) -> list[str]:
    """Owner decision 2026-10-25: during commissioning the Canon lane is the
    only development path. It advances one single-worker step and the legacy
    builders receive no products; without the versioned commissioning policy
    nothing is dispatched (the retired exclusivity file is never consulted).
    A lane failure is recorded and never crashes the supervisor loop."""
    from .iron_god_canon_lane import advance_commissioning_lane, load_policy

    if load_policy(repo_root) is None:
        atomic_json(state_root / "iron-god-canon" / "lane-status.json", {
            "schema_version": 1, "status": "COMMISSIONING_POLICY_UNAVAILABLE",
            "observed_at_utc": datetime.now(UTC).isoformat(),
        })
        return []
    try:
        canon = (advance or advance_commissioning_lane)(
            state_root, repo_root, supervisor=supervisor, release_sha=release_sha or "",
        )
        if advance is None:
            # Portfolio rotation: it always handles revocation, harvest and
            # publications; it dispatches only when commissioning is idle.
            from .iron_god_product_lane import advance_product_lane
            advance_product_lane(state_root, repo_root, supervisor=supervisor, release_sha=release_sha or "",
                                 allow_dispatch=(canon or {}).get("status") == "IDLE_NO_READY_SLICE")
    except Exception as exc:  # The lane must not take the supervisor down.
        atomic_json(state_root / "iron-god-canon" / "lane-status.json", {
            "schema_version": 1, "status": "LANE_ERROR",
            "error": f"{type(exc).__name__}: {str(exc)[:400]}",
            "observed_at_utc": datetime.now(UTC).isoformat(),
        })
    return []


def commissioning_active(repo_root: Path) -> bool:
    """True while the loaded release declares Iron God commissioning."""
    from .iron_god_canon_lane import load_policy

    return load_policy(repo_root) is not None


class SuspendedMergeWatcher:
    """Commissioning stand-in for the product PR merge watcher process.

    Owner decision 2026-10-25: no unrelated product development during
    commissioning, so green product PRs are not merged by the engine. It
    behaves like an already-exited process: nothing runs, nothing is merged.
    """

    pid = None

    def poll(self) -> int:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0
