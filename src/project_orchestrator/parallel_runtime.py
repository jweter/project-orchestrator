from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .execution_runtime import DEFAULT_EXECUTION_RUNTIMES, require_runtime_capabilities
from .parallel_worker_pool import ParallelWorkerPool
from .planning_dispatch import task_affinity_class, runner_productivity_history
from .recovery_manager import CLAIM_LEASE_SECONDS, worker_liveness
from .runner_registry import DEFAULT_RUNNERS, RunnerRegistry, default_runner_registry
from .runtime_leases import RuntimeLeasePool
from .state_io import atomic_json


def runner_requirements_for_task(task: dict) -> set[str]:
    raw = task.get("runner_requirements", ["code", "local", "windows", "offline"])
    if not isinstance(raw, list) or not raw or any(not isinstance(x, str) or not x.strip() for x in raw):
        raise RuntimeError("INVALID_RUNNER_REQUIREMENTS")
    return set(raw)


def select_task_runner(
    task: dict,
    registry: RunnerRegistry = DEFAULT_RUNNERS,
    running: dict[str, int] | None = None,
) -> str:
    if "runner_requirements" not in task and running is None:
        return "aider-ollama"
    return registry.select(runner_requirements_for_task(task), running).runner_id


def runtime_for_task(task: dict, require_capabilities_fn=require_runtime_capabilities) -> str:
    raw = task.get("runtime_requirements", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list) or any(not isinstance(x, str) or not x.strip() for x in raw):
        raise RuntimeError("INVALID_RUNTIME_REQUIREMENTS")
    requirements = set(raw)
    runtime_identity = "laptop-actor" if "laptop-actor" in requirements else None
    capabilities = requirements - {"laptop-actor"}
    machine_capabilities = {"unreal", "ollama"}
    if runtime_identity or capabilities & machine_capabilities:
        runtime_id = "laptop-actor"
        require_capabilities_fn(runtime_id, sorted(capabilities & machine_capabilities))
        return runtime_id
    return "native-windows"


def resource_leases_for_task(task: dict, runtime_id: str) -> tuple[str, ...]:
    raw = task.get("runtime_requirements", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list) or any(not isinstance(x, str) or not x.strip() for x in raw):
        raise RuntimeError("INVALID_RUNTIME_REQUIREMENTS")
    resources = []
    if "ollama" in raw:
        resources.append("resource:ollama")
    if "unreal" in raw:
        resources.append("resource:unreal")
    return tuple(resources)


def scarce_capacity_plan(
    batch: list[tuple[str, str, dict]],
    runtime_snapshot: dict,
) -> tuple[list[tuple[str, str, dict]], list[dict]]:
    """Stable-order runnable work ahead of tasks waiting on scarce local resources.

    The simulation consumes only the capacity visible at planning time.  It never
    grants authority; RuntimeLeasePool remains the execution-time fence.
    """
    capacities = runtime_snapshot.get("capacities", {})
    running = runtime_snapshot.get("running", {})
    available = {
        resource: max(int(capacity) - int(running.get(resource, 0)), 0)
        for resource, capacity in capacities.items()
        if str(resource).startswith("resource:")
    }
    runnable: list[tuple[str, str, dict]] = []
    deferred: list[tuple[str, str, dict]] = []
    evidence: list[dict] = []

    for repo, head, task in batch:
        resources = resource_leases_for_task(task, "laptop-actor")
        blocked = tuple(
            resource for resource in resources if available.get(resource, 0) <= 0
        )
        row = {
            "repository": repo,
            "head_sha": head,
            "slice_id": task.get("slice_id"),
            "runtime_requirements": list(task.get("runtime_requirements", []) or []),
            "scarce_resources": list(resources),
            "blocked_resources": list(blocked),
            "queue_state": "LOCAL_RESOURCE_WAIT" if blocked else "RUNNABLE",
        }
        evidence.append(row)
        if blocked:
            deferred.append((repo, head, task))
            continue
        runnable.append((repo, head, task))
        for resource in resources:
            if resource in available:
                available[resource] = max(available[resource] - 1, 0)

    return runnable + deferred, evidence


def runner_verified_history(state_root: Path) -> dict[str, dict[str, int]]:
    path = state_root / "hands-off-grant-lifecycle.json"
    try:
        grants = json.loads(path.read_text(encoding="utf-8")).get("grants", {})
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    history: dict[str, dict[str, int]] = {}
    for record in grants.values():
        if not isinstance(record, dict) or record.get("state") not in {"COMPLETED", "ABANDONED", "QUARANTINED"}:
            continue
        runner_id = record.get("runner_id") or (record.get("execution_slice") or {}).get("runner_id")
        if not isinstance(runner_id, str) or not runner_id:
            continue
        row = history.setdefault(runner_id, {"success": 0, "failure": 0})
        status = str(record.get("result_status") or "")
        if record.get("state") == "COMPLETED" and status not in {
            "NO_OP", "SELF_REPAIRED_TO_NO_OP", "BUILDER_FAILED", "PREFLIGHT_FAILED", "PUSH_FAILED", "PR_CREATE_FAILED"
        } and "FAILED" not in status:
            row["success"] += 1
        else:
            row["failure"] += 1
    return history


def runner_affinity_history(state_root: Path, task_class: str) -> dict[str, dict[str, int]]:
    path = state_root / "hands-off-grant-lifecycle.json"
    try:
        grants = json.loads(path.read_text(encoding="utf-8")).get("grants", {})
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    history: dict[str, dict[str, int]] = {}
    for record in grants.values():
        if not isinstance(record, dict) or record.get("state") not in {"COMPLETED", "ABANDONED", "QUARANTINED"}:
            continue
        authority = record.get("execution_slice") or {}
        if not isinstance(authority, dict) or task_affinity_class(authority) != task_class:
            continue
        runner_id = record.get("runner_id") or authority.get("runner_id")
        if not isinstance(runner_id, str) or not runner_id:
            continue
        row = history.setdefault(runner_id, {"success": 0, "failure": 0})
        status = str(record.get("result_status") or "")
        if record.get("state") == "COMPLETED" and "FAILED" not in status and status not in {
            "NO_OP", "SELF_REPAIRED_TO_NO_OP", "PREFLIGHT_FAILED", "PUSH_FAILED", "PR_CREATE_FAILED"
        }:
            row["success"] += 1
        else:
            row["failure"] += 1
    return history


def productivity_penalty(runner_id: str, stats: dict[str, dict[str, float]]) -> float:
    row = stats.get(runner_id)
    if not row or row["samples"] < 2 or row["seconds"] <= 0:
        return 0.0
    advancement_per_hour = row["verified_advancement"] * 3600.0 / row["seconds"]
    failure_rate = row["failures"] / row["samples"]
    repair_rate = row.get("repair_attempts", 0.0) / row["samples"]
    merge_rate = row.get("merged_verified", 0.0) / row["samples"]
    reality_rate = row.get("product_reality_verified", 0.0) / row["samples"]
    throughput_penalty = 1.0 / (1.0 + advancement_per_hour)
    merge_penalty = 1.0 - merge_rate
    reality_penalty = 1.0 - reality_rate
    return min(
        0.5,
        0.15 * throughput_penalty
        + 0.15 * failure_rate
        + 0.10 * min(repair_rate, 1.0)
        + 0.05 * merge_penalty
        + 0.05 * reality_penalty,
    )


def adaptive_runner_score(
    manifest,
    history: dict[str, dict[str, int]],
    running: dict[str, int],
    affinity: dict[str, dict[str, int]] | None = None,
    productivity: dict[str, dict[str, float]] | None = None,
) -> tuple[float, int, str]:
    row = history.get(manifest.runner_id, {})
    successes = int(row.get("success", 0))
    failures = int(row.get("failure", 0))
    reliability = (successes + 1) / (successes + failures + 2)
    affinity_row = (affinity or {}).get(manifest.runner_id, {})
    affinity_success = int(affinity_row.get("success", 0))
    affinity_failure = int(affinity_row.get("failure", 0))
    affinity_reliability = (affinity_success + 1) / (affinity_success + affinity_failure + 2)
    affinity_weight = min(affinity_success + affinity_failure, 4) / 4
    blended = reliability * (1 - affinity_weight) + affinity_reliability * affinity_weight
    utilization = running.get(manifest.runner_id, 0) / manifest.max_concurrency
    return (
        utilization + (1.0 - blended) + productivity_penalty(manifest.runner_id, productivity or {}),
        manifest.priority,
        manifest.runner_id,
    )


def runner_capacity_cooldowns(
    state_root: Path,
    now: datetime | None = None,
    cooldown_seconds: int = 1800,
) -> set[str]:
    try:
        grants = json.loads(
            (state_root / "hands-off-grant-lifecycle.json").read_text(encoding="utf-8")
        ).get("grants", {})
    except (OSError, json.JSONDecodeError, TypeError):
        return set()
    current = now or datetime.now(UTC)
    cooled = set()
    for record in grants.values():
        if not isinstance(record, dict) or record.get("result_status") != "RUNNER_CAPACITY_DEFERRED":
            continue
        runner = record.get("runner_id") or (record.get("execution_slice") or {}).get("runner_id")
        try:
            finished = datetime.fromisoformat(record.get("finished_at", ""))
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            continue
        if isinstance(runner, str) and 0 <= (current - finished).total_seconds() < cooldown_seconds:
            cooled.add(runner)
    return cooled


def select_adaptive_runner(
    task: dict,
    state_root: Path,
    registry: RunnerRegistry,
    running: dict[str, int],
) -> str:
    cooled = runner_capacity_cooldowns(state_root)
    candidates = [
        m for m in registry.eligible(runner_requirements_for_task(task), running)
        if m.runner_id not in cooled
    ]
    if not candidates:
        raise RuntimeError("NO_ELIGIBLE_RUNNER")
    history = runner_verified_history(state_root)
    task_class = task_affinity_class(task)
    affinity = runner_affinity_history(state_root, task_class)
    productivity = runner_productivity_history(state_root, task_class)
    return min(
        candidates,
        key=lambda item: adaptive_runner_score(
            item, history, running, affinity, productivity
        ),
    ).runner_id


def durable_runner_occupancy(
    state_root: Path,
    now: datetime | None = None,
    *,
    liveness_probe: Callable[[dict | None], str] = worker_liveness,
) -> dict[str, int]:
    path = state_root / "hands-off-grant-lifecycle.json"
    try:
        grants = json.loads(path.read_text(encoding="utf-8")).get("grants", {})
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    running: dict[str, int] = {}
    current = now or datetime.now(UTC)
    for record in grants.values():
        if not isinstance(record, dict) or record.get("state") not in {"ISSUED", "CLAIMED", "RUNNING"}:
            continue
        if record.get("state") in {"CLAIMED", "RUNNING"}:
            stamp = record.get("last_heartbeat_at") or record.get("claimed_at")
            try:
                heartbeat = datetime.fromisoformat(stamp)
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                continue
            if (
                (current - heartbeat).total_seconds() > CLAIM_LEASE_SECONDS
                and liveness_probe(record.get("worker")) == "DEAD"
            ):
                continue
        runner_id = record.get("runner_id") or (record.get("execution_slice") or {}).get("runner_id")
        if isinstance(runner_id, str) and runner_id:
            running[runner_id] = running.get(runner_id, 0) + 1
    return running


def dispatch_parallel_builders(
    pool: ParallelWorkerPool,
    repo_root: Path,
    workspace_root: Path,
    state_root: Path,
    products: list[str],
    *,
    ready_task_batch_fn: Callable,
    run_builder_for_product_fn: Callable,
    runtime_leases: RuntimeLeasePool,
    runtime_lease_recovery: dict | None,
    runner_registry: RunnerRegistry | None = None,
    dispatch_invocation_id: str | None = None,
    new_worker_budget: int | None = None,
) -> list[str]:
    """Saturate safe product lanes without exceeding host admission capacity."""
    if new_worker_budget is not None and (
        type(new_worker_budget) is not int or new_worker_budget < 0
    ):
        raise ValueError("new_worker_budget must be nonnegative")
    dispatched = []
    blocked = []
    dispatch_invocation_id = dispatch_invocation_id or uuid.uuid4().hex
    try:
        activation = json.loads(
            (state_root / "hands-off-activation.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, TypeError):
        activation = {}

    task_batches = {
        product: ready_task_batch_fn(
            state_root, activation, product, pool.max_workers_per_product
        )
        for product in products
    }
    capacity_snapshot = runtime_leases.snapshot()
    verification_queue: dict[str, list[dict]] = {}
    for product, batch in list(task_batches.items()):
        ordered, queue_evidence = scarce_capacity_plan(batch, capacity_snapshot)
        task_batches[product] = ordered
        verification_queue[product] = queue_evidence
    atomic_json(
        state_root / "local-verification-queue.json",
        {
            "schema_version": 1,
            "policy": "RUNNABLE_BEFORE_LOCAL_RESOURCE_WAIT",
            "products": verification_queue,
            "runtime_capacity": capacity_snapshot,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
        },
    )
    for product, batch in task_batches.items():
        if not batch:
            direction_blocks = []
            repositories = activation.get("products", {}).get(product, {}).get("repositories", {})
            for repository, head in repositories.items():
                request_path = state_root / "direction-requests" / f"{repository.replace('/', '__')}.json"
                try:
                    request = json.loads(request_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, TypeError):
                    continue
                if (
                    request.get("status") == "DIRECTION_REQUIRED"
                    and request.get("repository") == repository
                    and request.get("exact_head") == head
                ):
                    direction_blocks.append({
                        "product": product,
                        "worker_id": None,
                        "repository": repository,
                        "slice_id": None,
                        "reason": "DIRECTION_REQUIRED",
                        "detail_reason": request.get("reason"),
                    })
            if direction_blocks:
                blocked.extend(direction_blocks)
            else:
                blocked.append({
                    "product": product,
                    "worker_id": None,
                    "repository": None,
                    "slice_id": None,
                    "reason": "NO_CONFLICT_FREE_READY_SLICE",
                })

    assigned = {product: 0 for product in products}
    runners = runner_registry or DEFAULT_RUNNERS
    default_registry_refreshed = runner_registry is not None
    runner_running = durable_runner_occupancy(state_root)

    for slot in pool.available_slots(products):
        # The caller may apply a stricter memory governor than the executor's
        # theoretical worker count. Never stop existing work to honor this cap.
        if new_worker_budget is not None and len(dispatched) >= new_worker_budget:
            break
        batch = task_batches.get(slot.product, [])
        index = assigned.get(slot.product, 0)
        if index >= len(batch):
            continue
        planned_target = batch[index]
        try:
            runner_task = planned_target[2]
            try:
                runner_id = select_adaptive_runner(
                    runner_task, state_root, runners, runner_running
                )
            except RuntimeError as runner_exc:
                if (
                    not default_registry_refreshed
                    and str(runner_exc).split(":", 1)[0] == "NO_ELIGIBLE_RUNNER"
                ):
                    # External runner health is mutable during a long-lived
                    # supervisor process. Refresh only when the cached registry
                    # would otherwise idle an eligible lane, keeping the normal
                    # dispatch path free of repeated auth probes.
                    runners = default_runner_registry()
                    default_registry_refreshed = True
                    runner_id = select_adaptive_runner(
                        runner_task, state_root, runners, runner_running
                    )
                else:
                    raise
            runtime_id = runtime_for_task(planned_target[2])
        except (RuntimeError, ValueError) as exc:
            blocked.append({
                "product": slot.product,
                "worker_id": slot.worker_id,
                "repository": planned_target[0],
                "slice_id": planned_target[2].get("slice_id"),
                "reason": str(exc).split(":", 1)[0] or type(exc).__name__,
            })
            continue

        planned_target[2]["runner_id"] = runner_id
        planned_target[2]["execution_runtime"] = runtime_id
        planned_target[2]["worker_id"] = slot.worker_id
        runtime = DEFAULT_EXECUTION_RUNTIMES.require(runtime_id)
        provenance = getattr(runtime, "provenance", None)
        if callable(provenance):
            planned_target[2]["runtime_provenance"] = provenance(
                planned_target[2].get("runtime_requirements", [])
            )
        else:
            planned_target[2]["runtime_provenance"] = {"runtime_id": runtime_id}

        if runner_id not in {"aider-ollama", "claude-code"}:
            blocked.append({
                "product": slot.product,
                "worker_id": slot.worker_id,
                "repository": planned_target[0],
                "slice_id": planned_target[2].get("slice_id"),
                "reason": "RUNNER_ADAPTER_UNAVAILABLE",
            })
            continue

        assigned[slot.product] = index + 1

        lease_ids: dict[str, str] = {}
        try:
            for resource_id in resource_leases_for_task(planned_target[2], runtime_id):
                lease = runtime_leases.acquire(
                    resource_id, f"{slot.worker_id}:{resource_id}"
                )
                lease_ids[resource_id] = lease.lease_id
            runtime_lease = runtime_leases.acquire(runtime_id, slot.worker_id)
            planned_target[2]["runtime_lease_id"] = runtime_lease.lease_id
            planned_target[2]["resource_lease_ids"] = lease_ids
        except RuntimeError as exc:
            blocked.append({
                "product": slot.product,
                "worker_id": slot.worker_id,
                "repository": planned_target[0],
                "slice_id": planned_target[2].get("slice_id"),
                "reason": str(exc).split(":", 1)[0] or "RUNTIME_LEASE_UNAVAILABLE",
            })
            for resource_id in lease_ids:
                runtime_leases.release(f"{slot.worker_id}:{resource_id}")
            continue

        def leased_builder(
            *args,
            _worker_id=slot.worker_id,
            _runtime_lease_id=runtime_lease.lease_id,
            _resources=tuple(lease_ids.items()),
            **kwargs,
        ):
            try:
                return run_builder_for_product_fn(*args, **kwargs)
            finally:
                runtime_leases.release(_worker_id, _runtime_lease_id)
                for resource_id, resource_lease_id in _resources:
                    runtime_leases.release(
                        f"{_worker_id}:{resource_id}", resource_lease_id
                    )

        try:
            worker_id = pool.submit(
                slot,
                leased_builder,
                repo_root,
                workspace_root,
                state_root,
                slot.product,
                slot.worker_id,
                planned_target,
            )
        except RuntimeError as exc:
            blocked.append({
                "product": slot.product,
                "worker_id": slot.worker_id,
                "repository": planned_target[0],
                "slice_id": planned_target[2].get("slice_id"),
                "reason": str(exc).split(":", 1)[0] or "WORKER_SUBMISSION_REJECTED",
            })
            runtime_leases.release(slot.worker_id)
            for resource_id in lease_ids:
                runtime_leases.release(f"{slot.worker_id}:{resource_id}")
            continue
        # Only successfully submitted workers occupy runner capacity.
        # Failed runtime leases or worker submission must not strand a free lane.
        runner_running[runner_id] = runner_running.get(runner_id, 0) + 1
        dispatched.append(worker_id)

    atomic_json(state_root / "dispatch-blocks.json", {
        "schema_version": 2,
        "invocation_id": dispatch_invocation_id,
        "blocked": blocked,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
    })
    atomic_json(state_root / "parallel-worker-pool.json", {
        "schema_version": 2,
        "pool": pool.snapshot(),
        "runtime_leases": runtime_leases.snapshot(),
        "dispatched": dispatched,
        "dispatch_invocation_id": dispatch_invocation_id,
        "runtime_lease_recovery": runtime_lease_recovery,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
    })
    return dispatched
