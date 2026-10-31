"""One-shot accepted-event dispatch adapter; never grants acceptance authority.

The caller supplies independently verified state, refs, handoffs, and a
strictly matched eligible worker task. No live controller is started here.
"""

from pathlib import Path

from project_orchestrator.local_dispatch_event_claim import (
    claim_accepted_next,
    invoke_once,
)
from project_orchestrator.local_acceptance_refill import select_next_after_acceptance
from typing import Mapping

# Repository-aware source-root configuration (2026-10-18): different real
# repositories legitimately use different top-level package layouts --
# project-orchestrator's own src/ layout versus a repository like
# knowledge-engine-core whose package lives directly at its own name
# (knowledge_engine/, no src/ directory at all). This is a closed,
# explicit, trusted mapping -- an unlisted repository defaults to "src/"
# unchanged, never an open "any non-tests/ prefix is fine" rule, so a
# handoff cannot smuggle an unexpected implementation-path shape in
# merely by avoiding the literal "tests/" prefix. Every existing handoff
# for every repository not listed here keeps its exact prior behavior.
REPOSITORY_SOURCE_ROOTS: dict[str, str] = {
    "jweter/knowledge-engine-core": "knowledge_engine/",
    "jweter/knowledge-engine-ai": "knowledge_engine_ai/",
    "jweter/knowledge-engine-web": "knowledge_engine_web/",
}
DEFAULT_SOURCE_ROOT = "src/"


def _source_root_for_repository(repository) -> str:
    if not isinstance(repository, str) or not repository:
        raise RuntimeError("DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH")
    return REPOSITORY_SOURCE_ROOTS.get(repository, DEFAULT_SOURCE_ROOT)


def build_expected_task(*, slice_id, accepted_sha, packet, worktree_root):
    """The one canonical task-construction function (2026-10-18, owner-
    reported integration defect: iron_god_orchestration_caller.py's own
    independent task builder joined forbidden/acceptance with ", " and
    did not .strip() the objective, while this function's own prompt
    construction used "; " and did .strip() -- two independently
    maintained implementations drifted, and dispatch's identity check
    caught the mismatch only AFTER a real worktree had already been
    provisioned).

    Used both to VALIDATE a caller-supplied task
    (_require_exact_handoff_task, below) and to CONSTRUCT a task in the
    first place (iron_god_orchestration_caller.py imports and calls
    this directly) -- both callers get byte-identical results by
    construction, not by two parallel implementations staying
    coincidentally in sync.

    Raises RuntimeError("DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH") on
    any malformed, missing, or inconsistent packet field -- the same
    validation this function always performed as part of computing
    "expected", now also the validation a caller gets for free when
    constructing a task in the first place.
    """
    if not isinstance(packet, Mapping):
        raise RuntimeError("DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH")
    name = packet.get("worktree_name")
    allowed = packet.get("allowed_paths")
    forbidden = packet.get("forbidden", [])
    acceptance = packet.get("acceptance", [])
    objective = packet.get("objective")
    branch = packet.get("branch")
    repository = packet.get("repository")
    if (
        packet.get("schema_version") != 1
        or packet.get("slice_id") != slice_id
        or packet.get("state") != "ELIGIBLE"
        or packet.get("exact_base_sha") != accepted_sha
        or not isinstance(name, str)
        or not name
        or Path(name).name != name
        or not isinstance(branch, str)
        or not branch.startswith("iron-god/")
        or not isinstance(objective, str)
        or not objective.strip()
        or not isinstance(allowed, list)
        or len(allowed) != 2
        or any(
            not isinstance(path, str)
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            for path in allowed
        )
        or not isinstance(forbidden, list)
        or any(not isinstance(value, str) for value in forbidden)
        or not isinstance(acceptance, list)
        or any(not isinstance(value, str) for value in acceptance)
    ):
        raise RuntimeError("DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH")
    source_root = _source_root_for_repository(repository)
    sources = [path for path in allowed if path.startswith(source_root)]
    tests = [path for path in allowed if path.startswith("tests/")]
    if len(sources) != 1 or len(tests) != 1:
        raise RuntimeError("DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH")
    root = Path(worktree_root).resolve()
    work = (root / name).resolve()
    if work.parent != root:
        raise RuntimeError("DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH")
    # Reject symlink/junction escapes: resolve each declared path against
    # the REAL worktree (following any symlink/junction actually present
    # on disk) and require the resolved target to still be inside `work`.
    # The string-level checks above (no "..", not absolute) only catch a
    # traversal attempt spelled out in the path text itself -- a symlink
    # or junction planted at one of these path segments could otherwise
    # point outside the worktree while the declared path text still looks
    # innocent. This check is additive; it never loosens the checks above.
    for path in allowed:
        if not (work / path).resolve().is_relative_to(work):
            raise RuntimeError("DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH")
    prompt = (
        "Objective: "
        + objective.strip()
        + " Allowed paths: "
        + ", ".join(allowed)
        + ". Forbidden: "
        + "; ".join(forbidden)
        + ". Acceptance: "
        + "; ".join(acceptance)
    )
    return {
        "id": slice_id,
        "base_sha": accepted_sha,
        "branch": branch,
        "work": work,
        "source": sources[0],
        "tests": tests[0],
        "prompt": prompt,
    }


def _require_exact_handoff_task(
    *, slice_id, accepted_sha, handoffs, task, worktree_root
):
    """Validate the complete resident handoff contract before any durable claim."""
    packet = handoffs.get(slice_id)
    if not isinstance(task, Mapping):
        raise RuntimeError("DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH")
    expected = build_expected_task(
        slice_id=slice_id,
        accepted_sha=accepted_sha,
        packet=packet,
        worktree_root=worktree_root,
    )
    if (
        any(task.get(key) != value for key, value in expected.items())
        or task.get("repair") is True
        or task.get("dispatch_claim_token") is not None
    ):
        raise RuntimeError("DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH")


def dispatch_one_accepted_event(
    *,
    state,
    refs,
    owner_state_root: Path,
    milestone_id: str,
    controller,
    handoffs,
    claims_path: Path,
    worktree_root: Path,
    task,
    worker_executor,
    active_slice_ids=(),
):
    """Claim -> durable invocation reservation -> independently fenced worker.

    An uncertain invocation remains reserved; it must never be retried here.
    """
    # Check the exact selected task BEFORE writing an irreversible claim.
    # A mismatched task must not poison the durable event/slice claim store.
    selection = select_next_after_acceptance(
        state=state,
        refs=refs,
        owner_state_root=owner_state_root,
        milestone_id=milestone_id,
        controller=controller,
        handoffs=handoffs,
        active_slice_ids=tuple(active_slice_ids),
    )
    if selection.status != "READY_FOR_LEASED_DISPATCH":
        raise RuntimeError("DISPATCH_SELECTOR_NOT_READY:" + selection.reason)
    if (
        task.get("id") != selection.next_slice_id
        or task.get("base_sha") != selection.accepted_sha
    ):
        raise RuntimeError("DISPATCH_CLAIM_TASK_IDENTITY_MISMATCH")
    _require_exact_handoff_task(
        slice_id=selection.next_slice_id,
        accepted_sha=selection.accepted_sha,
        handoffs=handoffs,
        task=task,
        worktree_root=worktree_root,
    )

    claim = claim_accepted_next(
        state=state,
        refs=refs,
        owner_state_root=owner_state_root,
        milestone_id=milestone_id,
        controller=controller,
        handoffs=handoffs,
        claims_path=claims_path,
        active_slice_ids=tuple(active_slice_ids),
    )
    if (
        claim.event_id != selection.acceptance_event_id
        or claim.slice_id != selection.next_slice_id
        or claim.accepted_sha != selection.accepted_sha
    ):
        # Claim may now be durable: never launch or silently reclaim.
        raise RuntimeError("DISPATCH_SELECTION_CHANGED_AFTER_CLAIM")
    _require_exact_handoff_task(
        slice_id=claim.slice_id,
        accepted_sha=claim.accepted_sha,
        handoffs=handoffs,
        task=task,
        worktree_root=worktree_root,
    )

    def revalidate() -> bool:
        current = select_next_after_acceptance(
            state=state,
            refs=refs,
            owner_state_root=owner_state_root,
            milestone_id=milestone_id,
            controller=controller,
            handoffs=handoffs,
            active_slice_ids=tuple(active_slice_ids),
        )
        try:
            _require_exact_handoff_task(
                slice_id=claim.slice_id,
                accepted_sha=claim.accepted_sha,
                handoffs=handoffs,
                task=task,
                worktree_root=worktree_root,
            )
        except (RuntimeError, TypeError, ValueError):
            return False
        return (
            current.status == "READY_FOR_LEASED_DISPATCH"
            and current.acceptance_event_id == claim.event_id
            and current.next_slice_id == claim.slice_id == task.get("id")
            and current.accepted_sha == claim.accepted_sha == task.get("base_sha")
        )

    return invoke_once(
        claim=claim,
        claims_path=claims_path,
        owner_state_root=owner_state_root,
        revalidate=revalidate,
        executor=lambda reserved: worker_executor(reserved, task),
    )
