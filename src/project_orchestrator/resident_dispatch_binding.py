"""RDI-001 isolated candidate: bind an existing accepted event to resident dispatch.

This is a callable integration seam, not a CLI, controller activation, or a
second claim/lease system. The resident entrypoint must supply its existing
reserved-claim executor and independently retain its owner/resource mutex.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .local_first_state import LocalFirstState
from .local_git_refs import LocalGitRefs
from .resident_dispatch_authority import (
    ResidentDispatchBlocked,
    require_existing_dispatch_authority,
)


def dispatch_existing_accepted_event(
    *,
    controller: Mapping[str, object],
    owner_state_root: Path,
    expected_owner_state_root: Path,
    state_db: Path,
    repo: Path,
    milestone_id: str,
    handoffs: Mapping[str, Mapping[str, Any]],
    claims_path: Path,
    worktree_root: Path,
    task: Mapping[str, Any],
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
    """
    require_existing_dispatch_authority(
        controller=controller,
        owner_state_root=owner_state_root,
        expected_owner_state_root=expected_owner_state_root,
        state_db=state_db,
        repo=repo,
    )
    if not isinstance(milestone_id, str) or not milestone_id.strip():
        raise ResidentDispatchBlocked("ACCEPTED_MILESTONE_ID_MISSING")
    if not isinstance(handoffs, Mapping) or not isinstance(task, Mapping):
        raise ResidentDispatchBlocked("DISPATCH_INPUTS_UNVERIFIED")
    if not callable(worker_executor):
        raise ResidentDispatchBlocked("RESERVED_WORKER_EXECUTOR_MISSING")

    # Reject an absent protected baseline before opening the writable SQLite
    # state constructor. A candidate flag must not bootstrap authority.
    refs = LocalGitRefs(Path(repo), owner_state_root=Path(owner_state_root))
    if refs.accepted() is None:
        raise ResidentDispatchBlocked("ACCEPTED_BASELINE_MISSING")

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
