from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .decision_learning import planning_hint
from .portfolio_advancement import build_resilient_advancement_plan
from .state_io import atomic_json

PRODUCT_BY_REPOSITORY = {
    "jweter/knowledge-engine-core": "knowledge-engine",
    "jweter/knowledge-engine-web": "knowledge-engine",
    "jweter/knowledge-engine-ai": "knowledge-engine",
    "jweter/rocksmith-cdlc-generator": "rocksmith",
    "jweter/Project-Everward": "everward",
    "jweter/VoiceStudio": "voicestudio",
}


def product_for_repository(repository: str | None) -> str | None:
    return PRODUCT_BY_REPOSITORY.get(repository) if repository else None


def reserved_quarantine_repositories(state_root: Path) -> set[str]:
    path = state_root / "hands-off-grant-lifecycle.json"
    try:
        grants = json.loads(path.read_text(encoding="utf-8")).get("grants", {})
    except (OSError, json.JSONDecodeError, TypeError):
        return set()
    reserved = set()
    for record in grants.values():
        if not isinstance(record, dict):
            continue
        if record.get("state") == "QUARANTINED" or record.get("recovery_state") == "QUARANTINED":
            repo = record.get("repository")
            if isinstance(repo, str) and repo:
                reserved.add(repo)
    return reserved


def decision_learning_hint(state_root: Path, decision_class: str) -> dict:
    try:
        payload = json.loads((state_root / "decision-learning.json").read_text(encoding="utf-8"))
        learning = payload.get("learning", {})
    except (OSError, json.JSONDecodeError, TypeError):
        learning = {"policies": []}
    return planning_hint(learning, decision_class)


def slice_paths_conflict(left: dict, right: dict) -> bool:
    """Fail closed unless two slices prove disjoint mutation authority."""
    a = left.get("allowed_paths")
    b = right.get("allowed_paths")
    if not isinstance(a, list) or not isinstance(b, list) or not a or not b:
        return True

    def norm(path: object) -> str:
        return str(path).replace("\\", "/").strip("/")

    for x in map(norm, a):
        for y in map(norm, b):
            if not x or not y or x == y or x.startswith(y + "/") or y.startswith(x + "/"):
                return True
    return False


def select_conflict_free_slices(slices: list[dict], limit: int | None = None) -> list[dict]:
    selected: list[dict] = []
    for candidate in slices:
        if any(slice_paths_conflict(candidate, owned) for owned in selected):
            continue
        selected.append(candidate)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def task_affinity_class(task: dict) -> str:
    explicit = task.get("task_class")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    text = " ".join(str(task.get(key) or "") for key in ("objective", "slice_id", "capability_id")).lower()
    paths = " ".join(str(x).lower() for x in task.get("allowed_paths", []) if isinstance(x, str))
    material = f"{text} {paths}"
    rules = (
        ("tests", ("test", "pytest", "spec")),
        ("documentation", ("docs/", "readme", "documentation")),
        ("unreal", ("unreal", ".uproject", "blueprint")),
        ("refactor", ("refactor", "cleanup", "restructure")),
        ("repair", ("fix", "repair", "bug", "failure")),
    )
    for label, tokens in rules:
        if any(token in material for token in tokens):
            return label
    return "code"


def runner_productivity_history(state_root: Path, task_class: str | None = None) -> dict[str, dict[str, float]]:
    path = state_root / "hands-off-grant-lifecycle.json"
    try:
        grants = json.loads(path.read_text(encoding="utf-8")).get("grants", {})
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    stats: dict[str, dict[str, float]] = {}
    for record in grants.values():
        if not isinstance(record, dict) or record.get("state") not in {"COMPLETED", "ABANDONED", "QUARANTINED"}:
            continue
        authority = record.get("execution_slice") or {}
        if task_class is not None and (not isinstance(authority, dict) or task_affinity_class(authority) != task_class):
            continue
        runner_id = record.get("runner_id") or (authority.get("runner_id") if isinstance(authority, dict) else None)
        if not isinstance(runner_id, str) or not runner_id:
            continue
        try:
            explicit_seconds = record.get("execution_seconds")
            if explicit_seconds is not None:
                seconds = max(float(explicit_seconds), 1.0)
            else:
                started = datetime.fromisoformat(record.get("execution_started_at") or record.get("claimed_at") or record.get("issued_at"))
                finished = datetime.fromisoformat(record.get("execution_finished_at") or record.get("finished_at"))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                if finished.tzinfo is None:
                    finished = finished.replace(tzinfo=UTC)
                seconds = max((finished - started).total_seconds(), 1.0)
        except (TypeError, ValueError):
            continue
        row = stats.setdefault(runner_id, {
            "verified_advancement": 0.0, "successful_execution": 0.0, "seconds": 0.0,
            "failures": 0.0, "samples": 0.0, "repair_attempts": 0.0,
            "merged_verified": 0.0, "product_reality_verified": 0.0,
        })
        status = str(record.get("result_status") or "")
        success = record.get("state") == "COMPLETED" and "FAILED" not in status and status not in {
            "PREFLIGHT_FAILED", "PUSH_FAILED", "PR_CREATE_FAILED"
        }
        quality = record.get("outcome_quality")
        verified = quality in {"MERGED_VERIFIED", "PRODUCT_REALITY_VERIFIED"}
        row["successful_execution"] += 1.0 if success else 0.0
        row["verified_advancement"] += 1.0 if verified else 0.0
        row["failures"] += 0.0 if success else 1.0
        row["seconds"] += seconds
        row["samples"] += 1.0
        try:
            row["repair_attempts"] += max(float(record.get("repair_attempts", 0)), 0.0)
        except (TypeError, ValueError):
            pass
        if verified:
            row["merged_verified"] += 1.0
        if quality == "PRODUCT_REALITY_VERIFIED":
            row["product_reality_verified"] += 1.0
    return stats


def task_outcome_priority(state_root: Path, task: dict) -> tuple[float, int, str]:
    task_class = task_affinity_class(task)
    history = runner_productivity_history(state_root, task_class)
    samples = sum(row.get("samples", 0.0) for row in history.values())
    if samples <= 0:
        return (-0.20, 0, task_class)
    reality = sum(row.get("product_reality_verified", 0.0) for row in history.values()) / samples
    merged = sum(row.get("merged_verified", 0.0) for row in history.values()) / samples
    failures = sum(row.get("failures", 0.0) for row in history.values()) / samples
    outcome = 0.60 * reality + 0.30 * merged - 0.10 * failures
    exploration = 0.20 * max(0.0, (4.0 - samples) / 4.0)
    return (-(outcome + exploration), int(samples), task_class)


def planner_decision_id(product: str, repo: str, head: str, task: dict) -> str:
    material = f"{product}\0{repo}\0{head}\0{task.get('slice_id','')}\0{task.get('capability_id','')}"
    return hashlib.sha256(material.encode()).hexdigest()[:24]


def persist_planner_decision(
    state_root: Path, product: str, candidates: list, selected: list
) -> None:
    rows = []
    selected_ids = {(repo, task.get("slice_id")) for repo, _, task in selected}
    for repo, head, task in candidates:
        priority = task_outcome_priority(state_root, task)
        rows.append({
            "decision_id": planner_decision_id(product, repo, head, task),
            "repository": repo,
            "head_sha": head,
            "slice_id": task.get("slice_id"),
            "task_class": priority[2],
            "priority_score": priority[0],
            "samples": priority[1],
            "selected": (repo, task.get("slice_id")) in selected_ids,
        })
    atomic_json(
        state_root / "planner-decisions" / f"{product}.json",
        {
            "schema_version": 1,
            "product": product,
            "recorded_at": datetime.now(UTC).isoformat(),
            "candidate_count": len(rows),
            "selected_count": len(selected),
            "candidates": rows,
        },
    )


def ready_task_batch(
    state_root: Path,
    activation: dict,
    product: str,
    limit: int,
    *,
    derive_slices_fn: Callable,
    conflict_check_fn: Callable,
) -> list[tuple[str, str, dict]]:
    candidates: list[tuple[str, str, dict]] = []
    repositories = activation.get("products", {}).get(product, {}).get("repositories", {})
    for repo, head in sorted(repositories.items()):
        if not head:
            continue
        path = state_root / "capability-graphs" / f"{repo.replace('/','__')}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Missing planning authority is not a scheduling conflict. Persist
            # the real blocker so provisioning/re-planning can repair the lane.
            direction_dir = state_root / "direction-requests"
            direction_dir.mkdir(parents=True, exist_ok=True)
            atomic_json(direction_dir / f"{repo.replace('/', '__')}.json", {
                "schema_version": 1,
                "status": "DIRECTION_REQUIRED",
                "repository": repo,
                "exact_head": head,
                "reason": "CAPABILITY_GRAPH_REQUIRED",
                "issues": [{"code": "CAPABILITY_GRAPH_REQUIRED", "detail": type(exc).__name__}],
                "requested_at_utc": datetime.now(UTC).isoformat(),
            })
            continue
        derived = derive_slices_fn(payload, repository=repo, exact_head=head)
        request_path = state_root / "direction-requests" / f"{repo.replace('/', '__')}.json"
        if derived.get("status") == "DIRECTION_REQUIRED":
            direction_dir = request_path.parent
            direction_dir.mkdir(parents=True, exist_ok=True)
            atomic_json(request_path, {
                "schema_version": 1,
                "status": "DIRECTION_REQUIRED",
                "repository": repo,
                "exact_head": head,
                "reason": "CAPABILITY_AUTHORITY_NOT_EXECUTABLE",
                "issues": derived.get("issues", []),
                "requested_at_utc": datetime.now(UTC).isoformat(),
            })
            continue
        if derived.get("status") == "NO_WORK":
            request_path.unlink(missing_ok=True)
            if derived.get("reason") == "ROADMAP_EXHAUSTED":
                atomic_json(state_root / "continuation-request.json", {
                    "requested_at_utc": datetime.now(UTC).isoformat(),
                    "reason": "REFRESH_AUTHORIZED_CANDIDATES",
                    "repository": repo,
                    "exact_head": head,
                    "trigger": "ROADMAP_EXHAUSTED",
                })
            continue
        if derived.get("status") != "READY":
            continue
        request_path.unlink(missing_ok=True)
        for task in derived.get("slices", []):
            if conflict_check_fn(state_root, repo, task) is None:
                candidates.append((repo, head, task))
    candidates.sort(
        key=lambda item: (
            task_outcome_priority(state_root, item[2]),
            item[0],
            item[2].get("slice_id", ""),
        )
    )
    selected: list[tuple[str, str, dict]] = []
    for candidate in candidates:
        repo, head, task = candidate
        if any(repo == r and slice_paths_conflict(task, other) for r, _, other in selected):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    persist_planner_decision(state_root, product, candidates, selected)
    return selected


def _rotate_after(items: list[str], previous: str | None) -> list[str]:
    if not items or previous not in items:
        return items
    index = items.index(previous) + 1
    return items[index:] + items[:index]


def select_execution_target(
    state_root: Path,
    activation: dict,
    products: list[str],
    *,
    load_execution_slice_fn: Callable,
    conflict_check_fn: Callable,
    usability_policy: dict | None = None,
) -> tuple[str, str]:
    """Allocate one execution target with product fairness and optional usability pressure."""
    reserved = reserved_quarantine_repositories(state_root)
    product_repositories: dict[str, list[tuple[str, str]]] = {}
    for product in products:
        repositories = activation.get("products", {}).get(product, {}).get("repositories", {})
        ready_repositories = sorted(
            (repo, head)
            for repo, head in repositories.items()
            if head and repo not in reserved
        )
        if ready_repositories:
            product_repositories[product] = ready_repositories

    if not product_repositories:
        raise RuntimeError("NO_EXECUTION_TARGET")

    cursor_path = state_root / "execution-target-cursor.json"
    try:
        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
        previous_product = cursor.get("product")
        previous_repository = cursor.get("repository")
        repository_cursors = cursor.get("repositories", {})
        starvation = cursor.get("product_starvation", {})
        if not isinstance(repository_cursors, dict):
            repository_cursors = {}
        if not isinstance(starvation, dict):
            starvation = {}
        if previous_product and previous_repository and previous_product not in repository_cursors:
            repository_cursors[previous_product] = previous_repository
    except (OSError, json.JSONDecodeError, TypeError):
        previous_product = None
        previous_repository = None
        repository_cursors = {}
        starvation = {}

    atomic_json(
        state_root / "decision-learning-hint.json",
        {
            "schema_version": 1,
            "decision_class": "portfolio:allocation",
            "hint": decision_learning_hint(state_root, "portfolio:allocation"),
            "recorded_at_utc": datetime.now(UTC).isoformat(),
        },
    )

    product_order = _rotate_after(
        [product for product in products if product in product_repositories],
        previous_product,
    )
    blocked: list[str] = []
    decisions: list[dict] = []
    legal_candidates: list[dict] = []

    for product in product_order:
        repositories = product_repositories[product]
        repository_names = [repo for repo, _ in repositories]
        product_previous_repository = repository_cursors.get(product)
        if product_previous_repository in repository_names:
            ordered_names = _rotate_after(repository_names, product_previous_repository)
            by_name = {repo: head for repo, head in repositories}
            ordered_repositories = [(repo, by_name[repo]) for repo in ordered_names]
        else:
            ordered_repositories = repositories

        product_had_ready_slice = False
        for repo, head in ordered_repositories:
            try:
                execution = load_execution_slice_fn(state_root, repo, head)
            except RuntimeError as exc:
                reason = str(exc)
                decisions.append({
                    "repository": repo,
                    "product": product,
                    "head_sha": head,
                    "outcome": "NOT_READY",
                    "reason": reason,
                })
                blocked.append(f"{repo}={reason}")
                continue
            if execution.get("status") == "NO_WORK":
                reason = str(execution.get("reason") or "NO_WORK")
                decisions.append({
                    "repository": repo,
                    "product": product,
                    "head_sha": head,
                    "outcome": (
                        "ROADMAP_EXHAUSTED"
                        if reason == "ROADMAP_EXHAUSTED"
                        else "NO_AUTHORIZED_SLICE"
                    ),
                    "reason": reason,
                })
                blocked.append(f"{repo}={reason}")
                continue
            product_had_ready_slice = True
            conflict = conflict_check_fn(state_root, repo, execution)
            if conflict:
                decisions.append({
                    "repository": repo,
                    "product": product,
                    "head_sha": head,
                    "outcome": "ALREADY_OWNED",
                    "reason": "NONTERMINAL_GRANT",
                    "grant_id": conflict.get("grant_id"),
                    "grant_state": conflict.get("state"),
                })
                blocked.append(f"{repo}=ALREADY_OWNED:{conflict.get('grant_id')}")
                continue

            legal_candidates.append({
                "product": product,
                "repository": repo,
                "head_sha": head,
                "slice_id": execution.get("slice_id"),
                "eligible": True,
                "advances_usability_gate": execution.get("advances_usability_gate") is True,
                "removes_usability_blocker": execution.get("removes_usability_blocker") is True,
                "execution": execution,
            })
            decisions.append({
                "repository": repo,
                "product": product,
                "head_sha": head,
                "slice_id": execution.get("slice_id"),
                "outcome": "READY_CANDIDATE",
                "reason": "READY_EXECUTION_SLICE",
                "advances_usability_gate": execution.get("advances_usability_gate") is True,
                "removes_usability_blocker": execution.get("removes_usability_blocker") is True,
            })
            break

        if product_had_ready_slice and not any(row["product"] == product for row in legal_candidates):
            decisions.append({
                "repository": None,
                "product": product,
                "head_sha": None,
                "outcome": "PRODUCT_CAPACITY_OCCUPIED",
                "reason": "ALL_READY_SLICES_OWNED",
            })

    selected_candidate = None
    policy_active = isinstance(usability_policy, dict) and bool(usability_policy.get("products"))
    if legal_candidates:
        if policy_active:
            if not starvation:
                # Stable initial ordering preserves declared product order while
                # subsequent calls use persisted starvation age.
                starvation = {
                    product: len(product_order) - index
                    for index, product in enumerate(product_order)
                }
            advancement_plan = build_resilient_advancement_plan(
                legal_candidates,
                usability_policy,
                slots=1,
                advancement_age=starvation,
            )
            if advancement_plan["selected"]:
                selected_candidate = advancement_plan["selected"][0]
        if selected_candidate is None:
            selected_candidate = legal_candidates[0]

    if selected_candidate is not None:
        product = selected_candidate["product"]
        repo = selected_candidate["repository"]
        head = selected_candidate["head_sha"]
        for row in decisions:
            if row.get("product") == product and row.get("repository") == repo and row.get("outcome") == "READY_CANDIDATE":
                row["outcome"] = "SELECTED"
                row["reason"] = (
                    "USABILITY_WEIGHTED_READY_SLICE"
                    if policy_active else "READY_EXECUTION_SLICE"
                )
                row["starvation_age"] = int(starvation.get(product, 0))
                break
        if policy_active:
            for candidate in legal_candidates:
                candidate_product = candidate["product"]
                starvation[candidate_product] = max(int(starvation.get(candidate_product, 0)), 0) + 1
            starvation[product] = 0
        allocation_policy = (
            "USABILITY_WEIGHTED_FAIR_SHARE"
            if policy_active else "PRODUCT_FIRST_FAIR_SHARE"
        )
        atomic_json(
            state_root / "dispatch-decision.json",
            {
                "schema_version": 3,
                "allocation_policy": allocation_policy,
                "recorded_at_utc": datetime.now(UTC).isoformat(),
                "selected_repository": repo,
                "selected_product": product,
                "selected_slice_id": selected_candidate.get("slice_id"),
                "decisions": decisions,
            },
        )
        repository_cursors[product] = repo
        atomic_json(
            cursor_path,
            {
                "schema_version": 3,
                "product": product,
                "repository": repo,
                "repositories": repository_cursors,
                "product_starvation": starvation,
            },
        )
        return repo, head

    atomic_json(
        state_root / "dispatch-decision.json",
        {
            "schema_version": 3 if policy_active else 2,
            "allocation_policy": (
                "USABILITY_WEIGHTED_FAIR_SHARE"
                if policy_active else "PRODUCT_FIRST_FAIR_SHARE"
            ),
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "selected_repository": None,
            "selected_product": None,
            "decisions": decisions,
        },
    )
    terminal_outcomes = {"ROADMAP_EXHAUSTED", "NO_AUTHORIZED_SLICE"}
    terminal_decisions = [
        item for item in decisions
        if item.get("repository") is not None
    ]
    if terminal_decisions and all(
        item.get("outcome") in terminal_outcomes for item in terminal_decisions
    ):
        raise RuntimeError("ALL_EXECUTION_LANES_COMPLETE")
    detail = ";".join(blocked) or "UNKNOWN"
    raise RuntimeError(f"NO_READY_EXECUTION_SLICE:{detail}")

def productive_liveness(
    state_root: Path,
    authorized_products: list[str],
    now: datetime | None = None,
    stale_seconds: int = 900,
    builder_status: str | None = None,
) -> dict:
    """Expose recent productive flow without confusing NO_WORK with no authority."""
    current = now or datetime.now(UTC)
    lifecycle = state_root / "hands-off-grant-lifecycle.json"
    try:
        grants = json.loads(lifecycle.read_text(encoding="utf-8")).get("grants", {})
    except (OSError, json.JSONDecodeError, TypeError):
        grants = {}

    records = []
    latest = None
    latest_activity = None
    active = False
    for grant_id, record in grants.items():
        if not isinstance(record, dict):
            continue
        state = record.get("state")
        if state not in {"CLAIMED", "RUNNING", "COMPLETED"}:
            continue
        stamp = (
            record.get("last_productive_mutation_at")
            or record.get("stage_updated_at")
            or record.get("last_heartbeat_at")
            or record.get("finished_at")
            or record.get("claimed_at")
            or record.get("started_at")
        )
        parsed = None
        if isinstance(stamp, str):
            try:
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
        head_sha = record.get("head_sha")
        exact_head = record.get("exact_head")
        is_active = state in {"CLAIMED", "RUNNING"}
        # Active execution is liveness evidence, but a terminal worker is only
        # productive when it produced a new durable candidate head.  exact_head
        # is the admitted base and must never turn a null-head completion into
        # apparent production.
        produced_candidate = (
            state == "COMPLETED"
            and isinstance(head_sha, str)
            and bool(head_sha)
            and head_sha != exact_head
        )
        if parsed and produced_candidate and (latest is None or parsed > latest):
            latest = parsed
        if parsed and is_active and (latest_activity is None or parsed > latest_activity):
            latest_activity = parsed
        active = active or is_active
        records.append({
            "grant_id": grant_id,
            "repository": record.get("repository"),
            "slice_id": record.get("slice_id"),
            "head_sha": head_sha,
            "exact_head": exact_head,
            "produced_candidate": produced_candidate,
            "stage": record.get("execution_stage") or record.get("stage") or state,
            "state": state,
            "progress_at": stamp,
        })

    age = (current - latest).total_seconds() if latest else None
    activity_age = (current - latest_activity).total_seconds() if latest_activity else None
    no_slice = builder_status == "NO_WORK"
    eligible = bool(authorized_products)
    productive = bool(records) and age is not None and age <= stale_seconds

    if not authorized_products:
        status = "NO_AUTHORIZED_WORK"
    elif productive:
        status = "PRODUCTIVE"
    elif active and activity_age is not None and activity_age > stale_seconds:
        status = "STALE_ACTIVE_WORK"
    elif active:
        status = "ACTIVE_WORK"
    elif no_slice:
        status = "AUTHORIZED_NO_SLICE"
    else:
        status = "ELIGIBLE_IDLE"

    return {
        "status": status,
        "productive": productive,
        "eligible_work": eligible,
        "authorized_products": sorted(authorized_products),
        "grant_evidence": records,
        "last_productive_mutation_at": latest.isoformat() if latest else None,
        "seconds_since_productive_mutation": age,
        "seconds_since_active_progress": activity_age,
        "stale_after_seconds": stale_seconds,
    }


def product_convergence(
    state_root: Path,
    authorized_products: list[str],
    *,
    current_invocation_id: str | None = None,
) -> dict:
    authorized = {
        p for p in authorized_products
        if p in {"knowledge-engine", "rocksmith", "everward", "voicestudio"}
    }
    active = {}
    try:
        grants = json.loads(
            (state_root / "hands-off-grant-lifecycle.json").read_text(encoding="utf-8")
        ).get("grants", {})
    except (OSError, json.JSONDecodeError, TypeError):
        grants = {}

    for grant_id, record in grants.items():
        if not isinstance(record, dict) or record.get("state") not in {"ISSUED", "CLAIMED", "RUNNING"}:
            continue
        repository = record.get("repository")
        authority = record.get("execution_slice")
        if not repository and isinstance(authority, dict):
            repository = authority.get("repository")
        if not repository:
            repositories = [
                value for value in record.get("authorized_repositories", [])
                if isinstance(value, str) and value
            ]
            if len(repositories) == 1:
                repository = repositories[0]
        product = product_for_repository(repository)
        if product in authorized:
            active[product] = {
                "grant_id": grant_id,
                "repository": repository,
                "state": record.get("state"),
            }

    # A freshly launched builder can be authoritative before its lifecycle ledger
    # transition is visible. Accept only identity-bearing BUILDER_RUNNING records
    # whose grant still exists on disk; malformed/partial worker state fails closed.
    worker_root = state_root / "worker-state"
    if worker_root.exists():
        for builder_path in worker_root.glob("*/builder.json"):
            try:
                worker = json.loads(builder_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if not isinstance(worker, dict) or worker.get("status") != "BUILDER_RUNNING":
                continue
            worker_id = worker.get("worker_id")
            grant_id = worker.get("grant_id")
            invocation_id = worker.get("invocation_id")
            identity = worker.get("worker")
            if not all(isinstance(value, str) and value for value in (worker_id, grant_id, invocation_id)):
                continue
            if not isinstance(identity, dict) or not identity.get("pid") or not identity.get("process_start_fingerprint"):
                continue
            grant_path = state_root / "hands-off-execution-grants" / f"{grant_id}.json"
            try:
                grant = json.loads(grant_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            repository = grant.get("repository")
            authority = grant.get("execution_slice")
            if not repository and isinstance(authority, dict):
                repository = authority.get("repository")
            product = product_for_repository(repository)
            if product in authorized and product not in active:
                active[product] = {
                    "grant_id": grant_id,
                    "repository": repository,
                    "state": "BUILDER_RUNNING",
                    "worker_id": worker_id,
                    "invocation_id": invocation_id,
                }

    reasons = {}
    try:
        blocks = json.loads((state_root / "dispatch-blocks.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        blocks = {}
    blocks_are_fresh = (
        isinstance(blocks, dict)
        and current_invocation_id is not None
        and blocks.get("invocation_id") == current_invocation_id
    )
    for item in blocks.get("blocked", []) if blocks_are_fresh else []:
        if isinstance(item, dict) and item.get("product") in authorized:
            reasons[item["product"]] = {
                "reason": item.get("reason", "DISPATCH_BLOCKED"),
                "repository": item.get("repository"),
                "slice_id": item.get("slice_id"),
            }

    try:
        decision = json.loads((state_root / "dispatch-decision.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        decision = {}
    decision_is_fresh = (
        isinstance(decision, dict)
        and current_invocation_id is not None
        and decision.get("invocation_id") == current_invocation_id
    )
    for item in decision.get("decisions", []) if decision_is_fresh else []:
        if not isinstance(item, dict):
            continue
        product = item.get("product")
        outcome = item.get("outcome")
        if product in authorized and product not in active and outcome not in {None, "SELECTED"}:
            reasons.setdefault(product, []).append({
                "outcome": outcome,
                "reason": item.get("reason"),
                "repository": item.get("repository"),
            })

    missing = sorted(authorized - set(active) - set(reasons))
    status = "CONVERGED" if not missing else "UNEXPLAINED_ELIGIBLE_IDLE"
    payload = {
        "schema_version": 1,
        "status": status,
        "authorized_products": sorted(authorized),
        "active_products": active,
        "explained_nonexecuting_products": reasons,
        "dispatch_evidence_fresh": blocks_are_fresh or decision_is_fresh,
        "dispatch_block_evidence_fresh": blocks_are_fresh,
        "dispatch_decision_evidence_fresh": decision_is_fresh,
        "dispatch_invocation_id": current_invocation_id,
        "unexplained_eligible_products": missing,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
    }
    atomic_json(state_root / "product-convergence.json", payload)
    return payload


def record_eligible_idle_streaks(
    state_root: Path,
    authorized_products: list[str],
    *,
    current_invocation_id: str | None = None,
) -> dict:
    """Turn repeated authorized-but-unscheduled products into explicit P1 defects."""
    authorized = {
        p for p in authorized_products
        if p in {"knowledge-engine", "rocksmith", "everward", "voicestudio"}
    }
    try:
        previous = json.loads((state_root / "eligible-idle-streaks.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        previous = {"schema_version": 1, "products": {}}
    prior_products = previous.get("products", {}) if isinstance(previous, dict) else {}

    active_products = set()
    try:
        grants = json.loads(
            (state_root / "hands-off-grant-lifecycle.json").read_text(encoding="utf-8")
        ).get("grants", {})
    except (OSError, json.JSONDecodeError, TypeError):
        grants = {}
    active_grant_stale_seconds = 900
    current_time = datetime.now(UTC)
    for record in grants.values():
        if not isinstance(record, dict) or record.get("state") not in {"ISSUED", "CLAIMED", "RUNNING"}:
            continue
        stamp = (
            record.get("last_heartbeat_at")
            or record.get("stage_updated_at")
            or record.get("claimed_at")
            or record.get("issued_at")
        )
        try:
            observed_at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if (current_time - observed_at).total_seconds() > active_grant_stale_seconds:
            continue
        repository = record.get("repository")
        authority = record.get("execution_slice")
        if not repository and isinstance(authority, dict):
            repository = authority.get("repository")
        product = product_for_repository(repository)
        if product:
            active_products.add(product)

    try:
        decision = json.loads((state_root / "dispatch-decision.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        decision = {}
    decision_fresh = (
        isinstance(decision, dict)
        and current_invocation_id is not None
        and decision.get("invocation_id") == current_invocation_id
    )

    try:
        blocks = json.loads((state_root / "dispatch-blocks.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        blocks = {}
    blocks_fresh = (
        isinstance(blocks, dict)
        and current_invocation_id is not None
        and blocks.get("invocation_id") == current_invocation_id
    )

    current = {}
    for product in sorted(authorized):
        reason = None
        signature = None
        if product in active_products:
            reason = "ACTIVE_WORK"
        else:
            decisions = [
                row for row in decision.get("decisions", [])
                if decision_fresh and isinstance(row, dict) and row.get("product") == product
            ]
            no_slice = [row for row in decisions if row.get("outcome") == "NO_AUTHORIZED_SLICE"]
            if no_slice:
                reason = "NO_AUTHORIZED_SLICE"
                signature = [
                    [row.get("repository"), row.get("head_sha"), row.get("outcome")]
                    for row in no_slice
                ]
            if reason is None and blocks_fresh:
                product_blocks = [
                    row for row in blocks.get("blocked", [])
                    if isinstance(row, dict) and row.get("product") == product
                ]
                direction = [row for row in product_blocks if row.get("reason") == "DIRECTION_REQUIRED"]
                conflicts = [
                    row for row in product_blocks
                    if row.get("reason") == "NO_CONFLICT_FREE_READY_SLICE"
                ]
                if direction:
                    reason = "DIRECTION_REQUIRED"
                    signature = [
                        [row.get("repository"), row.get("detail_reason"), row.get("reason")]
                        for row in direction
                    ]
                elif conflicts:
                    reason = "NO_CONFLICT_FREE_READY_SLICE"
                    signature = [
                        [row.get("repository"), row.get("slice_id"), row.get("reason")]
                        for row in conflicts
                    ]

        prior = prior_products.get(product, {}) if isinstance(prior_products, dict) else {}
        idle_reason = reason in {"NO_AUTHORIZED_SLICE", "NO_CONFLICT_FREE_READY_SLICE"}
        same = idle_reason and prior.get("reason") == reason and prior.get("signature") == signature
        streak = int(prior.get("streak", 0)) + 1 if same else (1 if idle_reason else 0)
        current[product] = {
            "reason": reason or "NO_FRESH_IDLE_EVIDENCE",
            "signature": signature,
            "streak": streak,
            "p1_liveness_defect": streak >= 2,
        }

    defects = sorted(product for product, row in current.items() if row["p1_liveness_defect"])
    payload = {
        "schema_version": 1,
        "products": current,
        "p1_liveness_defects": defects,
        "status": "P1_LIVENESS_DEFECT" if defects else "OK",
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "dispatch_invocation_id": current_invocation_id,
    }
    atomic_json(state_root / "eligible-idle-streaks.json", payload)
    return payload


def request_liveness_reacquire(
    state_root: Path,
    liveness: dict,
    request_builder_fn: Callable[[Path], None],
) -> dict:
    status = liveness.get("status")
    if status not in {"ELIGIBLE_IDLE", "STALE_ACTIVE_WORK", "AUTHORIZED_NO_SLICE"}:
        return {"requested": False, "reason": "LIVENESS_RECOVERY_NOT_REQUIRED"}
    request_builder_fn(state_root)
    result = {
        "requested": True,
        "reason": status,
        "requested_at_utc": datetime.now(UTC).isoformat(),
        "active_claim_present": any(
            r.get("state") in {"CLAIMED", "RUNNING"}
            for r in liveness.get("grant_evidence", [])
            if isinstance(r, dict)
        ),
    }
    atomic_json(state_root / "liveness-reacquire.json", result)
    return result
