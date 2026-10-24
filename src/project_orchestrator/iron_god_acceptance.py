"""Local accepted-baseline advancement: the Integration Controller's
accept step.

Given a candidate's already-computed Verification Receipt
(iron_god_verification_receipt.py), decides whether to accept it and,
if so, atomically advances the accepted-baseline ref
(iron_god_refs.py) and permanently records a milestone ref for it
(iron_god_milestone_refs.py).

This module runs no verification itself -- it only acts on evidence
already produced elsewhere. It is never called automatically; every
invocation is an explicit, individually-reviewed action by a caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .iron_god_milestone_refs import MilestoneRefError, create_milestone
from .iron_god_refs import AcceptedBaselineError, advance_accepted_baseline
from .iron_god_verification_receipt import GREEN, VerificationReceipt


class AcceptanceError(RuntimeError):
    """Raised when a candidate cannot be safely accepted."""


@dataclass(frozen=True)
class AcceptanceResult:
    repository: str
    accepted_sha: str
    previous_baseline_sha: str | None
    milestone_id: str


def accept_candidate(
    repo_root: Path,
    repository: str,
    receipt: VerificationReceipt,
    *,
    expected_parent_sha: str | None,
    milestone_id: str,
) -> AcceptanceResult:
    """Accept `receipt`'s candidate as the new local accepted baseline.

    Fails closed (raises AcceptanceError, advances nothing) unless:
    - the receipt's own status is GREEN (every gate passed);
    - receipt.repository matches `repository` (never accept evidence
      produced for a different repository);
    - the accepted-baseline ref's current value exactly equals
      `expected_parent_sha`, and the candidate is a valid fast-forward
      from it (both enforced by advance_accepted_baseline itself).

    On success, the accepted-baseline ref advances and a permanent
    milestone ref is created pointing at the same commit. If the
    baseline advances but the milestone write then fails, this raises
    AcceptanceError -- the caller must re-inspect get_accepted_baseline
    and get_milestone to determine actual state before retrying, rather
    than assume nothing happened.
    """
    if receipt.status != GREEN:
        raise AcceptanceError(
            f"receipt status is {receipt.status!r}, not GREEN; refusing to accept"
        )
    if receipt.repository != repository:
        raise AcceptanceError(
            f"receipt.repository {receipt.repository!r} does not match "
            f"requested repository {repository!r}"
        )

    try:
        advance = advance_accepted_baseline(
            repo_root,
            repository,
            receipt.head_sha,
            expected_parent_sha=expected_parent_sha,
        )
    except AcceptedBaselineError as exc:
        raise AcceptanceError(f"could not advance accepted baseline: {exc}") from exc

    try:
        create_milestone(repo_root, milestone_id, advance.new_sha)
    except MilestoneRefError as exc:
        raise AcceptanceError(
            f"accepted baseline advanced to {advance.new_sha!r} but milestone "
            f"{milestone_id!r} could not be recorded: {exc}"
        ) from exc

    return AcceptanceResult(
        repository=repository,
        accepted_sha=advance.new_sha,
        previous_baseline_sha=advance.previous_sha,
        milestone_id=milestone_id,
    )
