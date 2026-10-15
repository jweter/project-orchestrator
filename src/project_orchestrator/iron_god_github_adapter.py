"""GitHub publication adapter -- CONTRACT ONLY.

Defines the interface any real GitHub-touching adapter must satisfy.
This module intentionally contains no implementation that pushes to a
Git remote or calls the GitHub API -- building and exercising a live
adapter is out of scope for this session (no GitHub operations are
authorized here). `NotImplementedAdapter` structurally satisfies the
contract so calling code can be written and tested against it today;
every one of its methods refuses to run. A future, separately-
authorized slice supplies the real implementation against this exact
contract without changing any caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .iron_god_publication_job import PublicationJob

_NOT_AUTHORIZED = (
    "GitHub operations are not authorized in this session; this adapter "
    "method must be implemented by a future, separately-authorized slice."
)


@dataclass(frozen=True)
class PushResult:
    ok: bool
    remote_branch: str
    error: str | None


@dataclass(frozen=True)
class PullRequestResult:
    ok: bool
    pr_number: int | None
    pr_url: str | None
    error: str | None


@runtime_checkable
class GitHubPublicationAdapter(Protocol):
    """Contract for a real GitHub-touching publication adapter.

    A conforming implementation must: never force-push; never bypass
    branch protection; never merge without the job's
    required_review_policy/required_ci having been independently
    confirmed by the caller; report every failure as data (a result
    object), never swallow it.
    """

    def push_branch(self, job: PublicationJob) -> PushResult: ...

    def open_pull_request(self, job: PublicationJob) -> PullRequestResult: ...

    def get_ci_status(self, job: PublicationJob) -> str: ...

    def get_review_status(self, job: PublicationJob) -> str: ...


class NotImplementedAdapter:
    """A conforming-shaped adapter whose every method refuses to run."""

    def push_branch(self, job: PublicationJob) -> PushResult:
        raise NotImplementedError(_NOT_AUTHORIZED)

    def open_pull_request(self, job: PublicationJob) -> PullRequestResult:
        raise NotImplementedError(_NOT_AUTHORIZED)

    def get_ci_status(self, job: PublicationJob) -> str:
        raise NotImplementedError(_NOT_AUTHORIZED)

    def get_review_status(self, job: PublicationJob) -> str:
        raise NotImplementedError(_NOT_AUTHORIZED)
