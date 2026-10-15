"""PublicationJob data model (roadmap section 9).

Defines and validates the shape of a publication job record -- the
durable unit a (not-yet-built) publication queue would track as an
accepted milestone moves toward GitHub. This module performs no GitHub
or network operation and no Git push; it only defines and validates
data.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

PUBLICATION_STATES = frozenset(
    {
        "READY_TO_PUBLISH",
        "PUSHING",
        "BRANCH_PUBLISHED",
        "PR_OPEN",
        "CI_PENDING",
        "REVIEW_PENDING",
        "MERGE_READY",
        "MERGED",
        "RELEASE_SYNCED",
        "PUBLICATION_DEFERRED",
        "CI_FAILED",
        "REVIEW_CHANGES_REQUIRED",
        "BASE_DIVERGED",
        "TRANSPORT_BLOCKED",
        "MANUAL_POLICY_GATE",
    }
)

TERMINAL_STATES = frozenset({"RELEASE_SYNCED"})

REQUIRED_FIELDS = (
    "publication_id",
    "repository",
    "milestone_id",
    "accepted_sha",
    "parent_accepted_sha",
    "github_base_sha",
    "verification_receipt_ref",
    "product_reality_receipt_ref",
    "required_ci",
    "required_review_policy",
    "publication_branch",
    "current_state",
)


class PublicationJobError(RuntimeError):
    """Raised when a publication job record cannot be validated safely."""


@dataclass(frozen=True)
class RetryAttempt:
    attempt_number: int
    occurred_at: float
    error: str | None


@dataclass(frozen=True)
class PublicationJob:
    publication_id: str
    repository: str
    milestone_id: str
    accepted_sha: str
    parent_accepted_sha: str | None
    github_base_sha: str | None
    verification_receipt_ref: str
    product_reality_receipt_ref: str | None
    required_ci: tuple[str, ...]
    required_review_policy: str
    publication_branch: str
    current_state: str
    retry_history: tuple[RetryAttempt, ...] = field(default_factory=tuple)
    last_error: str | None = None

    def is_terminal(self) -> bool:
        return self.current_state in TERMINAL_STATES

    def with_retry(self, attempt: RetryAttempt) -> "PublicationJob":
        """Return a new job with `attempt` appended to retry_history.
        PublicationJob is immutable (frozen) -- there is no method that
        mutates an existing instance in place."""
        return replace(
            self,
            retry_history=self.retry_history + (attempt,),
            last_error=attempt.error,
        )


def validate_publication_job(payload: dict) -> PublicationJob:
    """Parse and validate a raw publication-job payload. Fails closed on
    any missing required field or unknown current_state."""
    for name in REQUIRED_FIELDS:
        if name not in payload:
            raise PublicationJobError(
                f"publication job missing required field: {name!r}"
            )
    if payload["current_state"] not in PUBLICATION_STATES:
        raise PublicationJobError(
            f"unknown current_state: {payload['current_state']!r}"
        )

    retry_history = tuple(
        item if isinstance(item, RetryAttempt) else RetryAttempt(**item)
        for item in payload.get("retry_history", ())
    )

    return PublicationJob(
        publication_id=payload["publication_id"],
        repository=payload["repository"],
        milestone_id=payload["milestone_id"],
        accepted_sha=payload["accepted_sha"],
        parent_accepted_sha=payload["parent_accepted_sha"],
        github_base_sha=payload["github_base_sha"],
        verification_receipt_ref=payload["verification_receipt_ref"],
        product_reality_receipt_ref=payload["product_reality_receipt_ref"],
        required_ci=tuple(payload["required_ci"]),
        required_review_policy=payload["required_review_policy"],
        publication_branch=payload["publication_branch"],
        current_state=payload["current_state"],
        retry_history=retry_history,
        last_error=payload.get("last_error"),
    )
