"""RDI-001 isolated candidate: bind an existing accepted event to resident dispatch.

This is a callable integration seam, not a CLI, controller activation, or a
second claim/lease system. The resident entrypoint must supply its existing
reserved-claim executor and independently retain its owner/resource mutex.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .local_first_state import LocalFirstState
from .local_git_refs import LocalGitRefs
from .resident_dispatch_authority import (
    ResidentDispatchBlocked,
    require_existing_dispatch_authority,
)


def _require_current_controller_projection(
    *,
    controller: Mapping[str, object],
    controller_state_path: Path,
    expected_controller_state_path: Path,
) -> None:
    """Fail closed if the protected controller changed since caller admission.

    A copied candidate-mode mapping is not ongoing execution authority.
    The resident must pass its trusted CONTROLLER_STATE constant as the
    expected path, not a value supplied by an agent or a handoff packet.
    This read never creates or modifies the protected projection.
    """
    path = Path(controller_state_path)
    expected = Path(expected_controller_state_path)
    if (
        path.is_symlink()
        or not path.is_file()
        or path.resolve() != expected.resolve()
    ):
        raise ResidentDispatchBlocked("PROTECTED_CONTROLLER_PATH_UNVERIFIED")
    try:
        observed = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ResidentDispatchBlocked("PROTECTED_CONTROLLER_UNREADABLE") from exc
    if not isinstance(observed, dict) or observed != controller:
        raise ResidentDispatchBlocked("PROTECTED_CONTROLLER_STATE_CHANGED")


def dispatch_existing_accepted_event(
    *,
    controller: Mapping[str, object],
    controller_state_path: Path,
    expected_controller_state_path: Path,
    owner_state_root: Path,
    expected_owner_state_root: Path,
    state_db: Path,
    repo: Path,
    milestone_id: str | None,
    handoffs: Mapping[str, Mapping[str, Any]],
    claims_path: Path,
    worktree_root: Path,
    task: Mapping[str, Any] | None,
    worker_executor: Callable[..., Any],
    active_slice_ids: Sequence[str] = (),
) -> Any:
    """Delegate one fenced task; never create an acceptance baseline.

    Existing schema and owner authority are checked before opening the
    state constructor, which can initialize SQLite journal settings.
    The coordinator itself rechecks committed event/ref/task identity,
    atomically claims, and durably reserves before calling worker_executor.
    The resident-supplied worker_executor accepts keyword arguments
    claim, task, state, refs; state and refs are the exact objects passed
    to the coordinator. It must invoke only the existing reserved-claim path.
    Passing milestone_id=None and task=None derives both from the same
    committed acceptance ledger, protected refs, and eligible handoff.
    Missing or ambiguous evidence fails closed without a worker claim.
    """
    require_existing_dispatch_authority(
        controller=controller,
        owner_state_root=owner_state_root,
        expected_owner_state_root=expected_owner_state_root,
        state_db=state_db,
        repo=repo,
    )
    _require_current_controller_projection(
        controller=controller,
        controller_state_path=controller_state_path,
        expected_controller_state_path=expected_controller_state_path,
    )
    if milestone_id is not None and (
        not isinstance(milestone_id, str) or not milestone_id.strip()
    ):
        raise ResidentDispatchBlocked("ACCEPTED_MILESTONE_ID_MISSING")
    if not isinstance(handoffs, Mapping) or (
        task is not None and not isinstance(task, Mapping)
    ):
        raise ResidentDispatchBlocked("DISPATCH_INPUTS_UNVERIFIED")
    if not callable(worker_executor):
        raise ResidentDispatchBlocked("RESERVED_WORKER_EXECUTOR_MISSING")

    # Reject an absent protected baseline before opening the writable SQLite
    # state constructor. A candidate flag must not bootstrap authority.
    refs = LocalGitRefs(Path(repo), owner_state_root=Path(owner_state_root))
    accepted_sha = refs.accepted()
    if accepted_sha is None:
        raise ResidentDispatchBlocked("ACCEPTED_BASELINE_MISSING")
    if (
        not isinstance(accepted_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", accepted_sha) is None
    ):
        raise ResidentDispatchBlocked("ACCEPTED_BASELINE_INVALID")

    # Recheck owner/mode/ledger immediately before opening writable state.
    require_existing_dispatch_authority(
        controller=controller,
        owner_state_root=owner_state_root,
        expected_owner_state_root=expected_owner_state_root,
        state_db=state_db,
        repo=repo,
    )
    state = LocalFirstState(Path(state_db))

    # Recheck after constructor operations, before any durable claim.
    require_existing_dispatch_authority(
        controller=controller,
        owner_state_root=owner_state_root,
        expected_owner_state_root=expected_owner_state_root,
        state_db=state_db,
        repo=repo,
    )
    _require_current_controller_projection(
        controller=controller,
        controller_state_path=controller_state_path,
        expected_controller_state_path=expected_controller_state_path,
    )

    # A baseline moved while state was opened must never enter the coordinator.
    if refs.accepted() != accepted_sha:
        raise ResidentDispatchBlocked("ACCEPTED_BASELINE_CHANGED")

    # The resident loop does not receive a milestone ID from a worker.
    # Resolve it from the one COMMITTED event matching the protected accepted
    # baseline, rather than guessing from wall-clock order or GitHub main.
    if milestone_id is None:
        snapshot = state.snapshot()
        candidates = []
        for intent in snapshot["acceptance_intents"]:
            if (
                intent["state"] != "COMMITTED"
                or intent["accepted_sha"] != accepted_sha
            ):
                continue
            mid = intent["milestone_id"]
            milestones = [
                row for row in snapshot["milestones"]
                if row["milestone_id"] == mid
                and row["repository"] == intent["repository"]
                and row["accepted_sha"] == accepted_sha
                and row["verification_sha256"] == intent["verification_sha256"]
            ]
            events = [
                row for row in snapshot["journal"]
                if row["milestone_id"] == mid
                and row["event_type"] == "MILESTONE_ACCEPTED"
                and row["detail"] == accepted_sha
            ]
            if (
                len(milestones) == 1
                and len(events) == 1
                and refs.get("milestone", mid) == accepted_sha
            ):
                candidates.append(mid)
        if len(candidates) != 1:
            raise ResidentDispatchBlocked("ACCEPTED_EVENT_NOT_UNIQUE")
        milestone_id = candidates[0]

    # Derive the task from the SAME controller and handoff packet that the
    # coordinator will independently validate. Never manufacture an eligible
    # slice or use the legacy queue as a dispatch authority.
    if task is None:
        from .local_acceptance_refill import select_next_after_acceptance

        selection = select_next_after_acceptance(
            state=state,
            refs=refs,
            owner_state_root=Path(owner_state_root),
            milestone_id=milestone_id,
            controller=controller,
            handoffs=handoffs,
            active_slice_ids=tuple(active_slice_ids),
        )
        if selection.status != "READY_FOR_LEASED_DISPATCH":
            raise ResidentDispatchBlocked(
                "DISPATCH_SELECTOR_NOT_READY:" + selection.reason
            )
        packet = handoffs.get(selection.next_slice_id)
        if not isinstance(packet, Mapping):
            raise ResidentDispatchBlocked("DISPATCH_HANDOFF_UNVERIFIED")
        name = packet.get("worktree_name")
        branch = packet.get("branch")
        objective = packet.get("objective")
        allowed = packet.get("allowed_paths")
        forbidden = packet.get("forbidden", [])
        acceptance = packet.get("acceptance", [])
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or not isinstance(branch, str)
            or not isinstance(objective, str)
            or not isinstance(allowed, list)
            or any(not isinstance(value, str) for value in allowed)
            or not isinstance(forbidden, list)
            or any(not isinstance(value, str) for value in forbidden)
            or not isinstance(acceptance, list)
            or any(not isinstance(value, str) for value in acceptance)
        ):
            raise ResidentDispatchBlocked("DISPATCH_HANDOFF_UNVERIFIED")
        sources = [value for value in allowed if value.startswith("src/")]
        tests = [value for value in allowed if value.startswith("tests/")]
        if len(allowed) != 2 or len(sources) != 1 or len(tests) != 1:
            raise ResidentDispatchBlocked("DISPATCH_HANDOFF_UNVERIFIED")
        root = Path(worktree_root).resolve()
        # Preserve the original path until preclaim validation: resolving here
        # would erase a symlink/junction alias to another in-root worktree.
        work = root / name
        if work.resolve().parent != root:
            raise ResidentDispatchBlocked("DISPATCH_WORKTREE_ESCAPES_ROOT")
        task = {
            "id": selection.next_slice_id,
            "base_sha": selection.accepted_sha,
            "branch": branch,
            "work": work,
            "source": sources[0],
            "tests": tests[0],
            "parent_id": None,
            "repair": False,
            "prompt": (
                "Objective: " + objective.strip()
                + " Allowed paths: " + ", ".join(allowed)
                + ". Forbidden: " + "; ".join(forbidden)
                + ". Acceptance: " + "; ".join(acceptance)
            ),
        }

    # A missing, redirected, or unprovisioned worktree must not consume an
    # irreversible accepted-event claim. The embedded worker independently
    # checks Git branch/base/cleanliness again immediately before building.
    work = task.get("work")
    root = Path(worktree_root).resolve()
    if not isinstance(work, Path) or not root.is_dir():
        raise ResidentDispatchBlocked("DISPATCH_WORKTREE_NOT_READY")
    if (
        not work.is_dir()
        or work.is_symlink()
        or (hasattr(work, "is_junction") and work.is_junction())
        or work.resolve().parent != root
    ):
        raise ResidentDispatchBlocked("DISPATCH_WORKTREE_NOT_READY")

    # Recheck owner and current protected controller before any claim.
    require_existing_dispatch_authority(
        controller=controller,
        owner_state_root=owner_state_root,
        expected_owner_state_root=expected_owner_state_root,
        state_db=state_db,
        repo=repo,
    )
    _require_current_controller_projection(
        controller=controller,
        controller_state_path=controller_state_path,
        expected_controller_state_path=expected_controller_state_path,
    )
    if refs.accepted() != accepted_sha:
        raise ResidentDispatchBlocked("ACCEPTED_BASELINE_CHANGED")

    # Bind the reserved worker to the *same* ledger and Git-ref objects used
    # by the selector and durable coordinator. A separately constructed state
    # or ref reader could diverge between reservation and worker entry.
    def reserved_worker(claim: Any, selected_task: Mapping[str, Any]) -> Any:
        require_existing_dispatch_authority(
            controller=controller,
            owner_state_root=owner_state_root,
            expected_owner_state_root=expected_owner_state_root,
            state_db=state_db,
            repo=repo,
        )
        _require_current_controller_projection(
            controller=controller,
            controller_state_path=controller_state_path,
            expected_controller_state_path=expected_controller_state_path,
        )
        # The coordinator reserves exactly one invocation. A ref change after
        # reservation must fail closed, leaving reconciliation to durable proof.
        if refs.accepted() != accepted_sha:
            raise ResidentDispatchBlocked("ACCEPTED_BASELINE_CHANGED")
        return worker_executor(
            claim=claim,
            task=selected_task,
            state=state,
            refs=refs,
        )

    # The sole durable claim and invoke_once authority lives in this existing
    # coordinator. Do not call the builder or a legacy queue from this seam.
    from accepted_event_dispatch_coordinator import dispatch_one_accepted_event

    return dispatch_one_accepted_event(
        state=state,
        refs=refs,
        owner_state_root=Path(owner_state_root),
        milestone_id=milestone_id,
        controller=controller,
        handoffs=handoffs,
        claims_path=Path(claims_path),
        worktree_root=Path(worktree_root),
        task=task,
        worker_executor=reserved_worker,
        active_slice_ids=tuple(active_slice_ids),
    )
