"""Read-only IG-044 publication provenance inspection.

This is NOT claim, migration, release, or publication authority. The caller
must repeat verification under a fenced SQLite write transaction, use trusted
runner evidence, and check owner authorization immediately before commit.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from project_orchestrator.human_ownership import AUTHORIZED, owner_control_status
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs
from project_orchestrator.local_verification import (
    VerificationError,
    VerificationPolicy,
    verify_preflight,
)


class PublicationProvenanceError(ValueError):
    """Block ambiguous or unauthenticated local milestone evidence."""


@dataclass(frozen=True)
class InspectedMilestone:
    milestone_id: str
    repository: str
    accepted_sha: str
    receipt_sha256: str
    product_reality: str


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublicationProvenanceError("GIT_UNAVAILABLE") from exc
    if result.returncode not in (0, 1):
        raise PublicationProvenanceError("GIT_CHECK_FAILED")
    return result.returncode, result.stdout.strip()


def _single(rows: list[dict], milestone_id: str) -> dict:
    matches = [row for row in rows if row["milestone_id"] == milestone_id]
    if len(matches) != 1:
        raise PublicationProvenanceError("MILESTONE_RECORD_AMBIGUOUS")
    return matches[0]


def inspect_milestone(
    *,
    repo: Path,
    state: LocalFirstState,
    refs: LocalGitRefs,
    milestone_id: str,
    evidence_path: Path,
    policy: VerificationPolicy,
) -> InspectedMilestone:
    """Inspect existing evidence without changing Git, SQLite, or tasks."""
    if owner_control_status(refs.owner_state_root) != AUTHORIZED:
        raise PublicationProvenanceError("OWNER_CONTROL_VETO")
    if not milestone_id or "/" in milestone_id or "\\" in milestone_id:
        raise PublicationProvenanceError("INVALID_MILESTONE_ID")
    left = _git(Path(repo), "rev-parse", "--path-format=absolute", "--git-common-dir")
    right = _git(refs.repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if left[0] or right[0] or Path(left[1]).resolve() != Path(right[1]).resolve():
        raise PublicationProvenanceError("REPOSITORY_REF_STORE_MISMATCH")
    before = state.snapshot()
    intent = _single(before["acceptance_intents"], milestone_id)
    milestone = _single(before["milestones"], milestone_id)
    publication = _single(before["publications"], milestone_id)
    identity = (
        policy.repository,
        milestone["accepted_sha"],
        milestone["verification_sha256"],
        milestone["product_reality"],
    )
    intent_identity = (
        intent["repository"],
        intent["accepted_sha"],
        intent["verification_sha256"],
        intent["product_reality"],
    )
    if intent["state"] != "COMMITTED" or identity != intent_identity:
        raise PublicationProvenanceError("UNTRUSTED_ACCEPTANCE_INTENT")
    if (
        publication["repository"] != policy.repository
        or milestone["repository"] != policy.repository
    ):
        raise PublicationProvenanceError("REPOSITORY_MISMATCH")
    accepted_events = [
        event
        for event in before["journal"]
        if event["milestone_id"] == milestone_id
        and event["event_type"] == "MILESTONE_ACCEPTED"
    ]
    if (
        len(accepted_events) != 1
        or accepted_events[0]["detail"] != milestone["accepted_sha"]
    ):
        raise PublicationProvenanceError("ACCEPTANCE_JOURNAL_INVALID")
    target = milestone["accepted_sha"]
    if refs.get("milestone", milestone_id) != target:
        raise PublicationProvenanceError("MILESTONE_REF_UNVERIFIED")
    accepted = refs.accepted()
    if (
        accepted is None
        or _git(Path(repo), "merge-base", "--is-ancestor", target, accepted)[0] != 0
    ):
        raise PublicationProvenanceError("ACCEPTED_REF_ANCESTRY_UNVERIFIED")
    if (
        _git(
            Path(repo),
            "merge-base",
            "--is-ancestor",
            intent["accepted_base_sha"],
            target,
        )[0]
        != 0
    ):
        raise PublicationProvenanceError("ACCEPTED_BASE_ANCESTRY_UNVERIFIED")
    try:
        receipt = verify_preflight(
            repo=Path(repo),
            expected_sha=target,
            evidence_path=Path(evidence_path),
            policy=policy,
            owner_state_root=refs.owner_state_root,
        )
    except VerificationError as exc:
        raise PublicationProvenanceError("NATIVE_RECEIPT_UNVERIFIED") from exc
    if (
        receipt.evidence_sha256 != milestone["verification_sha256"]
        or receipt.product_reality != milestone["product_reality"]
    ):
        raise PublicationProvenanceError("ACCEPTANCE_RECEIPT_IDENTITY_CHANGED")
    if before != state.snapshot():
        raise PublicationProvenanceError("STATE_CHANGED_DURING_INSPECTION")
    if refs.get("milestone", milestone_id) != target or refs.accepted() != accepted:
        raise PublicationProvenanceError("GIT_REFS_CHANGED_DURING_INSPECTION")
    if owner_control_status(refs.owner_state_root) != AUTHORIZED:
        raise PublicationProvenanceError("OWNER_CONTROL_VETO")
    return InspectedMilestone(
        milestone_id,
        policy.repository,
        target,
        receipt.evidence_sha256,
        receipt.product_reality,
    )
