"""The orchestration caller: the integration layer that connects Iron
God's already-built, independently-tested primitives into one
repeating local-first development cycle, per the owner's explicit
2026-10-17/18 authorization to build it.

This module duplicates none of the primitives it sequences. Per
cycle, for one repository:

1. resolve_latest_committed_milestone (iron_god_milestone_resolution.py)
   -- the authoritative accepted development state.
2. local_acceptance_refill.select_next_after_acceptance (read-only) --
   which dependency-ready slice is next, without yet claiming it.
3. iron_god_allocation_gate.decide_allocation -- re-checked
   immediately before provisioning a worktree, same invariant
   IG-058's protected-work audit already re-verifies elsewhere.
4. provision_candidate_worktree -- creates the real, isolated Git
   worktree for that exact slice. This is the one genuinely new
   capability this module adds: an earlier investigation this session
   (RDI001_P3_ALLOCATION_GATE_INVESTIGATION, 2026-10-17) found no
   automated worktree-provisioning call site exists anywhere in this
   codebase -- worktree creation was, until now, always a human
   running `git worktree add` directly. This function is exactly the
   call site that investigation said decide_allocation should be
   wired into once built.
5/6. resident_dispatch_binding.dispatch_existing_accepted_event ->
   the real, unedited local_continuous_worker.execute_reserved_dispatch_claim
   -> execute_candidate_attempt -- durable claim, fenced lease, real
   isolated build, real verify_and_commit (ruff/pytest/26-gate
   preflight). local_continuous_worker.py is loaded via importlib and
   never edited.
7. iron_god_product_reality_verification.evaluate_product_reality_for_candidate
   -- the gate built this session.
8. iron_god_failure_classification.classify -- JER-37 classification
   for anything that is not VERIFIED_CANDIDATE, so a permission denial
   or owner-control restriction is never treated as a reason to retry
   differently; it stops the cycle and is reported, not routed.
9. iron_god_candidate_local_acceptance.accept_verified_candidate_as_next_milestone
   -- real local acceptance, gated by step 7, using the real
   LocalFirstState + LocalGitRefs mechanism proven in
   test_iron_god_complete_loop_proof.py.
10. The real evidence already recorded inside execute_candidate_attempt
    (slice_outcomes table) is read back for its receipt_sha256 rather
    than a second, invented progress record.
11. Loop again against the newly-accepted milestone, until max_cycles,
    no eligible slice remains, or a genuine blocker is hit.

Authorization: this orchestration caller has its OWN explicit
`orchestration_authorized` gate, supplied by the caller and never
defaulted True. This is in addition to, not instead of,
`controller["candidate_execution_authorized"]`, which the composed
primitives (select_next_after_acceptance, dispatch_existing_accepted_event)
still check exactly as before. This module never flips `mode` or
`candidate_execution_authorized` on anything, and never edits
local_continuous_worker.py -- it only calls already-authorized,
already-tested functions in sequence, and stops (never retries through
a different route) the moment any of them says no.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .iron_god_allocation_gate import ALLOCATION_PERMITTED, decide_allocation
from .iron_god_candidate_local_acceptance import (
    ACCEPTED,
    accept_verified_candidate_as_next_milestone,
)
from .iron_god_failure_classification import classify
from .iron_god_milestone_resolution import resolve_latest_committed_milestone
from .iron_god_product_reality_adapter import ProductRealityAdapter
from .local_acceptance_refill import select_next_after_acceptance
from .local_first_state import LocalFirstState
from .local_git_refs import LocalGitRefs

VERIFIED_CANDIDATE = "VERIFIED_CANDIDATE"

# The builder interface (2026-10-18, owner directive: "Iron God's
# orchestration layer should depend on a clearly defined builder
# interface, not on the details of how Claude Code happens to
# launch... The production builder should ultimately be replaceable
# without rewriting the orchestrator").
#
# This module does not itself take a builder parameter -- the real
# substitution point lives one layer down, inside the real
# local_continuous_worker.py's execute_candidate_attempt, which looks
# up its builder_fn/verifier_fn defaults from that module's OWN
# build_candidate/verify_and_commit globals at call time (`builder_fn
# = build_candidate if builder_fn is None else builder_fn`). Since
# local_continuous_worker.py is outside this session's permitted
# modification scope, that lookup cannot be threaded through as an
# explicit parameter of execute_reserved_dispatch_claim today. The
# existing, already-used substitution mechanism -- replacing the
# loaded worker module's `build_candidate`/`verify_and_commit`
# attributes before constructing `worker_executor` -- IS the builder
# interface boundary; every proof test this session (including
# test_iron_god_orchestration_caller.py's real two-slice chain) already
# depends on exactly this contract, not on anything claude-cli-specific.
# BuilderFn/VerifierFn below name that contract explicitly so a future
# caller (a different model, a different CLI, a non-LLM code generator)
# can be substituted by installing a different callable matching this
# same shape, without this orchestration module -- or its callers --
# changing at all.
BuilderFn = Callable[..., None]
"""`(task: dict, *, lease_heartbeat: Callable[[], None] | None = None) -> None`
-- writes real files into `task["work"]`. Must raise on failure, never
return a value. Installed by replacing the loaded worker module's
`build_candidate` attribute."""

VerifierFn = Callable[..., dict]
"""`(task: dict, *, lease_heartbeat: Callable[[], None] | None = None) ->
dict` -- returns at minimum `{"head_sha": str, "receipt_sha256": str,
"checks": int}` for the real candidate commit it verified. Must raise
`RuntimeError` on failure (see local_continuous_worker.py's own
`repairable` failure-code set for which codes route to repair rather
than propagate). Installed by replacing the loaded worker module's
`verify_and_commit` attribute."""


class OrchestrationNotAuthorizedError(RuntimeError):
    """Raised when run_continuous_development_cycle is called without
    its own explicit orchestration_authorized=True -- never defaulted,
    never inferred from controller["candidate_execution_authorized"]
    alone."""


class OrchestrationBlockedError(RuntimeError):
    """Raised when a genuine blocker stops the cycle before max_cycles
    is reached. Callers must inspect `.outcomes` for everything
    completed before the blocker -- nothing before this exception is
    lost or silently discarded."""

    def __init__(self, message: str, outcomes: list["CycleOutcome"]) -> None:
        super().__init__(message)
        self.outcomes = outcomes


@dataclass(frozen=True)
class CycleOutcome:
    cycle_index: int
    slice_id: str | None
    dispatch_status: str | None
    candidate_sha: str | None
    product_reality_status: str | None
    acceptance_status: str | None
    new_milestone_id: str | None
    stopped_reason: str | None


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def provision_candidate_worktree(
    repo: Path,
    *,
    worktree_root: Path,
    worktree_name: str,
    branch: str,
    base_sha: str,
) -> Path:
    """Create the real, isolated Git worktree a candidate slice builds
    in. Re-checks disk allocation immediately before creating it --
    the same "re-verify right before acting" discipline IG-058's
    protected-work audit already applies elsewhere, composed via the
    existing decide_allocation rather than re-derived.

    Idempotent: if the worktree already exists at exactly base_sha on
    exactly `branch`, returns it unchanged rather than failing or
    recreating it -- a crashed cycle must be able to resume, not
    duplicate provisioning work.
    """
    allocation = decide_allocation(worktree_root)
    if allocation.status != ALLOCATION_PERMITTED:
        raise OrchestrationBlockedError(
            f"worktree provisioning blocked by allocation gate: {allocation.reason}",
            outcomes=[],
        )

    target = worktree_root / worktree_name
    if target.exists():
        existing_branch = _git(target, "rev-parse", "--abbrev-ref", "HEAD")
        existing_head = _git(target, "rev-parse", "HEAD")
        if existing_branch == branch and existing_head == base_sha:
            return target
        raise OrchestrationBlockedError(
            f"worktree {worktree_name!r} already exists but does not match "
            f"the expected branch/base (expected {branch!r}@{base_sha}, found "
            f"{existing_branch!r}@{existing_head})",
            outcomes=[],
        )

    _git(repo, "worktree", "add", "-b", branch, str(target), base_sha)
    return target


def _build_task_for_slice(
    packet: Mapping[str, Any], *, slice_id: str, accepted_sha: str, worktree_root: Path
) -> dict:
    """Delegates entirely to the one canonical task-construction
    function, accepted_event_dispatch_coordinator.build_expected_task
    (2026-10-18, owner-reported integration defect: this function
    previously had its own independent prompt-construction logic --
    joining forbidden/acceptance with ", " and not .strip()-ing the
    objective -- that silently drifted from the coordinator's own
    "; "-joined, .strip()-ed version. The coordinator's own identity
    check caught the resulting mismatch, but only AFTER a real
    worktree had already been provisioned for the wrong task. Calling
    the SAME function the coordinator uses internally for validation
    makes this a structural guarantee, not something that depends on
    two independently maintained implementations staying in sync.

    Deliberately imported locally, not at module level:
    accepted_event_dispatch_coordinator.py is a bare module living in
    IronGodLocal, not part of the installed project_orchestrator
    package, and every real caller of this function already puts
    IronGodLocal on sys.path before importing anything from
    project_orchestrator (the same precondition every proof test and
    the standalone entry point this session built already satisfies).
    """
    from accepted_event_dispatch_coordinator import build_expected_task

    return build_expected_task(
        slice_id=slice_id,
        accepted_sha=accepted_sha,
        packet=packet,
        worktree_root=worktree_root,
    )


def _read_receipt_sha256(lease_path: Path, slice_id: str) -> str:
    db = sqlite3.connect(lease_path)
    try:
        row = db.execute(
            "SELECT evidence_json FROM slice_outcomes WHERE slice_id=?", (slice_id,)
        ).fetchone()
    finally:
        db.close()
    if row is None:
        raise OrchestrationBlockedError(
            f"no recorded slice outcome for {slice_id!r}; cannot derive a "
            "verification identity for local acceptance",
            outcomes=[],
        )
    evidence = json.loads(row[0])
    receipt = evidence.get("receipt_sha256")
    if not isinstance(receipt, str) or len(receipt) != 64:
        raise OrchestrationBlockedError(
            f"recorded slice outcome for {slice_id!r} has no valid 64-char "
            "receipt_sha256; refusing to accept on an unverifiable identity",
            outcomes=[],
        )
    return receipt


def run_continuous_development_cycle(
    *,
    orchestration_authorized: bool,
    dispatch_fn: Callable[..., dict],
    worker_executor: Callable[..., dict],
    state: LocalFirstState,
    state_db_path: Path,
    refs: LocalGitRefs,
    repo: Path,
    repository: str,
    owner_state_root: Path,
    controller: dict[str, Any],
    handoffs: dict[str, dict[str, Any]],
    claims_path: Path,
    lease_path: Path,
    worktree_root: Path,
    next_milestone_id_fn: Callable[[int], str],
    product_reality_adapter: ProductRealityAdapter | None,
    product_reality_required: bool,
    max_cycles: int,
    handoff_planner_fn: Callable[[dict, dict, str, str], None] | None = None,
) -> list[CycleOutcome]:
    """Repeat the accept -> select -> provision -> dispatch -> gate ->
    accept cycle for one repository, up to `max_cycles` times.

    `handoffs` is a plain, mutable dict this function may both read
    and write. A slice N+1 handoff packet's `exact_base_sha` must
    equal whatever slice N's own candidate commit turns out to be --
    unknowable before slice N actually builds. This module does not
    decompose the roadmap into slices itself (that is the already-built
    Capability Compiler's job, IG-060..067); `handoff_planner_fn`, if
    given, is called as `handoff_planner_fn(handoffs, controller,
    repository, new_accepted_sha)` immediately after each successful
    acceptance,
    so a real planner (or, in a test, a stand-in) can write the next
    slice's handoff packet into `handoffs` with the now-known base sha
    before the next cycle's selection runs. Without a planner, the
    cycle naturally stops at NO_DEPENDENCY_READY_SLICE once no
    pre-written handoff matches the newly-accepted sha -- it never
    invents a slice to keep going.

    Stops (raising OrchestrationBlockedError, never silently) the
    moment: no committed milestone exists yet for `repository`; the
    selector reports no dependency-ready slice; the allocation gate
    blocks provisioning; a dispatch raises anything other than a
    VERIFIED_CANDIDATE result (the failure is classified via
    iron_god_failure_classification.classify and reported, never
    retried through a different route); or Product Reality blocks
    acceptance. Returns every CycleOutcome completed before the stop
    (via the exception's `.outcomes`) or after `max_cycles` (via the
    normal return value) -- nothing silently discarded either way.
    """
    if not orchestration_authorized:
        raise OrchestrationNotAuthorizedError(
            "run_continuous_development_cycle requires its own explicit "
            "orchestration_authorized=True; never inferred or defaulted"
        )
    if max_cycles < 1:
        raise ValueError("max_cycles must be >= 1")

    outcomes: list[CycleOutcome] = []
    active_slice_ids: list[str] = []

    for cycle_index in range(max_cycles):
        latest = resolve_latest_committed_milestone(state.snapshot(), repository)
        if latest is None:
            raise OrchestrationBlockedError(
                "no committed milestone exists for this repository yet; a "
                "human-bootstrapped baseline is required before the "
                "orchestration caller can select a first slice",
                outcomes=outcomes,
            )

        decision = select_next_after_acceptance(
            state=state,
            refs=refs,
            owner_state_root=owner_state_root,
            milestone_id=latest.milestone_id,
            controller=controller,
            handoffs=handoffs,
            active_slice_ids=tuple(active_slice_ids),
        )
        if decision.status != "READY_FOR_LEASED_DISPATCH":
            raise OrchestrationBlockedError(
                f"selector not ready: {decision.status}:{decision.reason}",
                outcomes=outcomes,
            )

        slice_id = decision.next_slice_id
        packet = handoffs[slice_id]
        # Validate and build the task BEFORE provisioning any real Git
        # worktree (2026-10-18, owner-reviewer finding): an invalid
        # handoff (e.g. an unsupported path shape) must never leave
        # behind a real `git worktree add` with nothing to build in it.
        try:
            task = _build_task_for_slice(
                packet,
                slice_id=slice_id,
                accepted_sha=decision.accepted_sha,
                worktree_root=worktree_root,
            )
        except RuntimeError as exc:
            raise OrchestrationBlockedError(
                f"task construction failed for slice {slice_id!r} before any "
                f"worktree was provisioned: {exc}",
                outcomes=outcomes,
            ) from exc
        worktree = provision_candidate_worktree(
            repo,
            worktree_root=worktree_root,
            worktree_name=packet["worktree_name"],
            branch=packet["branch"],
            base_sha=packet["exact_base_sha"],
        )

        try:
            result = dispatch_fn(
                controller=controller,
                owner_state_root=owner_state_root,
                expected_owner_state_root=owner_state_root,
                state_db=state_db_path,
                repo=repo,
                milestone_id=latest.milestone_id,
                handoffs=handoffs,
                claims_path=claims_path,
                worktree_root=worktree_root,
                task=task,
                worker_executor=worker_executor,
                active_slice_ids=tuple(active_slice_ids),
            )
        except RuntimeError as exc:
            signal = str(exc).split(":", 1)[0]
            try:
                classified = classify(signal)
                stopped_reason = (
                    f"{classified.category}:{signal}:"
                    f"requires_owner_action={classified.requires_explicit_owner_action}"
                )
            except Exception:
                stopped_reason = f"UNCLASSIFIED:{exc}"
            outcomes.append(
                CycleOutcome(
                    cycle_index=cycle_index,
                    slice_id=slice_id,
                    dispatch_status=None,
                    candidate_sha=None,
                    product_reality_status=None,
                    acceptance_status=None,
                    new_milestone_id=None,
                    stopped_reason=stopped_reason,
                )
            )
            raise OrchestrationBlockedError(
                f"dispatch raised for slice {slice_id!r}: {stopped_reason}",
                outcomes=outcomes,
            ) from exc

        if result.get("status") != VERIFIED_CANDIDATE:
            outcomes.append(
                CycleOutcome(
                    cycle_index=cycle_index,
                    slice_id=slice_id,
                    dispatch_status=result.get("status"),
                    candidate_sha=None,
                    product_reality_status=None,
                    acceptance_status=None,
                    new_milestone_id=None,
                    stopped_reason=f"NOT_VERIFIED:{result.get('status')}",
                )
            )
            raise OrchestrationBlockedError(
                f"slice {slice_id!r} did not produce a VERIFIED_CANDIDATE "
                f"(status={result.get('status')!r}); routed to the existing "
                "repair/quarantine path, not retried here",
                outcomes=outcomes,
            )

        candidate_sha = result["candidate_sha"]
        receipt_sha256 = _read_receipt_sha256(lease_path, slice_id)
        new_milestone_id = next_milestone_id_fn(cycle_index)

        acceptance = accept_verified_candidate_as_next_milestone(
            state=state,
            refs=refs,
            repository=repository,
            base_sha=latest.accepted_sha,
            candidate_sha=candidate_sha,
            new_milestone_id=new_milestone_id,
            verification_sha256=receipt_sha256,
            product_reality_adapter=product_reality_adapter,
            product_reality_required=product_reality_required,
        )

        outcomes.append(
            CycleOutcome(
                cycle_index=cycle_index,
                slice_id=slice_id,
                dispatch_status=VERIFIED_CANDIDATE,
                candidate_sha=candidate_sha,
                product_reality_status=acceptance.product_reality.status,
                acceptance_status=acceptance.status,
                new_milestone_id=(
                    new_milestone_id if acceptance.status == ACCEPTED else None
                ),
                stopped_reason=None,
            )
        )

        if acceptance.status != ACCEPTED:
            raise OrchestrationBlockedError(
                f"slice {slice_id!r} verified but Product Reality blocked "
                f"acceptance: {acceptance.reason}",
                outcomes=outcomes,
            )

        active_slice_ids.append(slice_id)
        if handoff_planner_fn is not None:
            handoff_planner_fn(handoffs, controller, repository, candidate_sha)

    return outcomes
