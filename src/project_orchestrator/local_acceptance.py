"""IG-034 evidence-gated local acceptance controller.

This controller binds immutable Git refs, exact-head FULL preflight evidence,
the durable SQLite prepare/finalize transaction, and owner-control authority.
It deliberately has no Product Reality bypass: repositories whose policy
requires Product Reality remain blocked until the repository adapter exists.
"""

from __future__ import annotations

import json
from pathlib import Path

from project_orchestrator.human_ownership import AUTHORIZED, owner_control_status
from project_orchestrator.local_acceptance_recovery import classify_pending_acceptances
from project_orchestrator.local_first_state import LocalFirstState, LocalStateError
from project_orchestrator.local_git_refs import LocalGitRefError, LocalGitRefs
from project_orchestrator.local_verification import (
    VerificationError,
    VerificationPolicy,
    VerifiedPreflight,
    verify_preflight,
)


class AcceptanceError(ValueError):
    """Acceptance invariant failed. Stop rather than infer authority."""


def _same_repo(repo: Path, refs: LocalGitRefs) -> None:
    try:
        if Path(repo).resolve() != refs.repo.resolve():
            raise AcceptanceError("REPOSITORY_REF_STORE_MISMATCH")
    except OSError as exc:
        raise AcceptanceError("REPOSITORY_PATH_UNAVAILABLE") from exc


def _owner(refs: LocalGitRefs) -> None:
    if owner_control_status(refs.owner_state_root) != AUTHORIZED:
        raise AcceptanceError("OWNER_CONTROL_VETO")


def _verify(
    *,
    repo: Path,
    target_sha: str,
    evidence_path: Path,
    policy: VerificationPolicy,
    refs: LocalGitRefs,
) -> VerifiedPreflight:
    _owner(refs)
    try:
        receipt = verify_preflight(
            repo=repo,
            expected_sha=target_sha,
            evidence_path=evidence_path,
            policy=policy,
            owner_state_root=refs.owner_state_root,
        )
    except VerificationError as exc:
        raise AcceptanceError(f"VERIFICATION_FAILED:{exc}") from exc
    _owner(refs)
    return receipt


def prepare_verified_acceptance(
    *,
    repo: Path,
    state: LocalFirstState,
    refs: LocalGitRefs,
    milestone_id: str,
    accepted_base_sha: str,
    target_sha: str,
    evidence_path: Path,
    policy: VerificationPolicy,
) -> VerifiedPreflight:
    """Verify target, protect its milestone ref, then durably prepare SQLite.

    This stage never advances refs/iron-god/accepted.
    """
    _same_repo(repo, refs)
    receipt = _verify(
        repo=repo,
        target_sha=target_sha,
        evidence_path=evidence_path,
        policy=policy,
        refs=refs,
    )
    if receipt.repository != policy.repository or receipt.head_sha != target_sha:
        raise AcceptanceError("VERIFIED_RECEIPT_IDENTITY_MISMATCH")
    if refs.accepted() != accepted_base_sha:
        raise AcceptanceError("ACCEPTED_BASE_CHANGED")
    _owner(refs)
    try:
        refs.protect("milestone", milestone_id, target_sha)
    except LocalGitRefError as exc:
        raise AcceptanceError(f"MILESTONE_REF_FAILED:{exc}") from exc
    context = json.dumps(
        {
            "evidence_path": str(Path(evidence_path)),
            "result_count": receipt.result_count,
            "controller": "IG-034",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        state.prepare_acceptance(
            repository=receipt.repository,
            milestone_id=milestone_id,
            accepted_base_sha=accepted_base_sha,
            accepted_sha=target_sha,
            verification_sha256=receipt.evidence_sha256,
            product_reality=receipt.product_reality,
            context_json=context,
        )
    except LocalStateError as exc:
        raise AcceptanceError(f"STATE_PREPARE_FAILED:{exc}") from exc
    _owner(refs)
    return receipt


def commit_or_recover_prepared_acceptance(
    *,
    repo: Path,
    state: LocalFirstState,
    refs: LocalGitRefs,
    milestone_id: str,
    evidence_path: Path,
    policy: VerificationPolicy,
) -> VerifiedPreflight:
    """Revalidate a prepared intent, perform/recover Git CAS, then finalize.

    A crash after Git CAS but before SQLite commit is recoverable by replaying
    this function. Any divergence, missing protected ref, or evidence drift
    fails closed.
    """
    _same_repo(repo, refs)
    _owner(refs)
    intent = state.acceptance_intent(milestone_id)
    if intent is None:
        raise AcceptanceError("ACCEPTANCE_INTENT_MISSING")
    if intent["state"] == "COMMITTED":
        raise AcceptanceError("ACCEPTANCE_ALREADY_COMMITTED")
    if intent["repository"] != policy.repository:
        raise AcceptanceError("ACCEPTANCE_REPOSITORY_MISMATCH")

    recovery = {
        item.milestone_id: item
        for item in classify_pending_acceptances(state=state, refs=refs)
    }.get(milestone_id)
    if recovery is None:
        raise AcceptanceError("PREPARED_ACCEPTANCE_NOT_PENDING")
    allowed = {
        "CAS_NOT_DONE_REVALIDATE_BEFORE_ADVANCE",
        "CAS_DONE_REVALIDATE_BEFORE_FINALIZE",
    }
    if recovery.disposition not in allowed:
        raise AcceptanceError(f"RECOVERY_STOP:{recovery.disposition}")

    receipt = _verify(
        repo=repo,
        target_sha=intent["accepted_sha"],
        evidence_path=evidence_path,
        policy=policy,
        refs=refs,
    )
    if receipt.evidence_sha256 != intent["verification_sha256"]:
        raise AcceptanceError("VERIFICATION_RECEIPT_CHANGED")
    if receipt.product_reality != intent["product_reality"]:
        raise AcceptanceError("PRODUCT_REALITY_CHANGED")
    if refs.get("milestone", milestone_id) != intent["accepted_sha"]:
        raise AcceptanceError("MILESTONE_REF_CHANGED")

    if recovery.disposition == "CAS_NOT_DONE_REVALIDATE_BEFORE_ADVANCE":
        try:
            refs.advance_accepted(
                expected_sha=intent["accepted_base_sha"],
                new_sha=intent["accepted_sha"],
            )
        except LocalGitRefError as exc:
            raise AcceptanceError(f"ACCEPTED_CAS_FAILED:{exc}") from exc

    _owner(refs)
    if refs.accepted() != intent["accepted_sha"]:
        raise AcceptanceError("ACCEPTED_REF_NOT_TARGET")
    if refs.get("milestone", milestone_id) != intent["accepted_sha"]:
        raise AcceptanceError("MILESTONE_REF_CHANGED")

    # Re-read the exact native receipt after the CAS boundary. This is what
    # makes crash replay safe without treating the SQLite intent as authority.
    receipt_after = _verify(
        repo=repo,
        target_sha=intent["accepted_sha"],
        evidence_path=evidence_path,
        policy=policy,
        refs=refs,
    )
    if (
        receipt_after.evidence_sha256 != intent["verification_sha256"]
        or receipt_after.product_reality != intent["product_reality"]
    ):
        raise AcceptanceError("POST_CAS_VERIFICATION_CHANGED")
    try:
        state.finish_prepared_acceptance(
            milestone_id=milestone_id,
            expected_sha=intent["accepted_sha"],
            expected_verification_sha256=intent["verification_sha256"],
        )
    except LocalStateError as exc:
        raise AcceptanceError(f"STATE_FINALIZE_FAILED:{exc}") from exc
    _owner(refs)
    return receipt_after
