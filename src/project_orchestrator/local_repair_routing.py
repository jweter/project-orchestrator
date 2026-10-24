"""IG-035 deterministic repair routing for Phase 3 failures.

Routing is intentionally side-effect free. It converts fail-closed diagnostics
from integration, verification, acceptance, Git-ref and SQLite state gates into
one explicit repair lane. It never retries, mutates refs, changes SQLite, or
weakens a failed gate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RepairDirective:
    lane: str
    action: str
    retry_after_repair: bool
    preserve_candidate: bool = True
    owner_release_required: bool = False


def _message(error: BaseException | str) -> str:
    text = str(error)
    return text.upper().replace(" ", "_")


def route_phase3_failure(error: BaseException | str) -> RepairDirective:
    """Return one fail-closed repair directive for a Phase 3 diagnostic."""
    diagnostic = _message(error)

    if "OWNER_CONTROL" in diagnostic or "HUMAN_OWNED" in diagnostic:
        return RepairDirective(
            lane="OWNER_HANDOFF",
            action="WAIT_FOR_EXPLICIT_OWNER_RELEASE",
            retry_after_repair=True,
            owner_release_required=True,
        )

    if "PRODUCT_REALITY" in diagnostic:
        return RepairDirective(
            lane="PRODUCT_REALITY_REPAIR",
            action="RUN_REPOSITORY_PRODUCT_REALITY_ADAPTER",
            retry_after_repair=True,
        )

    if "INTEGRATION_CONFLICT" in diagnostic:
        return RepairDirective(
            lane="INTEGRATION_CONFLICT_REPAIR",
            action="REPAIR_CONFLICT_ON_NEW_CANDIDATE",
            retry_after_repair=True,
        )

    if (
        "ACCEPTED_BASE_CHANGED" in diagnostic
        or "ACCEPTED_COMPARE_AND_SWAP" in diagnostic
        or "ACCEPTED_CAS_FAILED" in diagnostic
        or "ACCEPTED_REF_DIVERGED" in diagnostic
    ):
        return RepairDirective(
            lane="STALE_BASE_REINTEGRATION",
            action="RELOAD_ACCEPTED_BASE_AND_REINTEGRATE",
            retry_after_repair=True,
        )

    if (
        "REPOSITORY_ACCEPTANCE_LANE_OCCUPIED" in diagnostic
        or "PREPARED_ACCEPTANCE_NOT_PENDING" in diagnostic
        or "ACCEPTANCE_INTENT" in diagnostic
        or "ACCEPTANCE_ALREADY_COMMITTED" in diagnostic
    ):
        return RepairDirective(
            lane="ACCEPTANCE_RECONCILIATION",
            action="RECONCILE_DURABLE_ACCEPTANCE_INTENT",
            retry_after_repair=True,
        )

    if (
        "MILESTONE_REF" in diagnostic
        or "PROTECTED_REF" in diagnostic
        or "RECOVERY_STOP" in diagnostic
    ):
        return RepairDirective(
            lane="AUTHORITY_RECONCILIATION",
            action="RECONCILE_PROTECTED_GIT_AUTHORITY",
            retry_after_repair=False,
        )

    if (
        "VERIFICATION" in diagnostic
        or "PREFLIGHT" in diagnostic
        or "EXACT_HEAD" in diagnostic
        or "WORKTREE" in diagnostic
        or "CHECK_NOT_GREEN" in diagnostic
        or "RECEIPT_CHANGED" in diagnostic
        or "CANDIDATE_REF_MISMATCH" in diagnostic
    ):
        return RepairDirective(
            lane="VERIFICATION_REPAIR",
            action="REPAIR_THEN_RERUN_EXACT_HEAD_FULL_PREFLIGHT",
            retry_after_repair=True,
        )

    if (
        "GIT_EXECUTION" in diagnostic
        or "GIT_COMMAND_FAILED" in diagnostic
        or "_FAILED:" in diagnostic
    ):
        return RepairDirective(
            lane="INFRASTRUCTURE_REPAIR",
            action="PRESERVE_STATE_AND_REPAIR_LOCAL_EXECUTION",
            retry_after_repair=True,
        )

    return RepairDirective(
        lane="MANUAL_TRIAGE",
        action="STOP_AND_CAPTURE_UNCLASSIFIED_DIAGNOSTIC",
        retry_after_repair=False,
    )
