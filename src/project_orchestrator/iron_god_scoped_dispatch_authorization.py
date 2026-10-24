"""Repository-scoped execution boundary for the worker-dispatch call site.

Owner directive (2026-10-18, "ENGINE RECOVERY AND AUTHORIZATION
RESOLUTION", item 5): the existing `candidate_execution_authorized` flag
in the real controller ledger is global and repository-unaware -- flipping
it authorizes candidate execution for ANY repository with an eligible
handoff, not only the one the owner intends. The owner explicitly
declined to flip that flag "simply to get our first worker running," and
a prior attempt to add an authorization-scoping parameter directly to
`execute_reserved_dispatch_claim` inside the off-limits, non-version-
controlled `local_continuous_worker.py` was denied twice by the platform's
own "Security Weaken" safety classifier and was not retried.

This module adds a real, enforced, independently testable repository
boundary WITHOUT touching that file and WITHOUT depending on the global
flag's own semantics changing at all. It sits strictly BEFORE the real
worker executor in the call chain: if the task's declared repository is
not in the caller-supplied authorized set, the real executor is never
invoked, so the question of whether the global flag happens to be True
never even arises for an out-of-scope repository. This is additive to
the existing authorization checks inside `execute_candidate_attempt`
(gate(), resource checks, the global flag) -- it narrows what can reach
them, it does not replace or weaken them.

This module is decision-only: it does not dispatch, claim, lease, or
build anything. The real standalone entrypoint must still be wired to
pass a `scoped_worker_executor` partial (binding `authorized_repositories`
and `inner_executor=execute_reserved_dispatch_claim`) as its
`worker_executor` argument to `dispatch_one_accepted_event` -- that wiring
change is deliberately NOT made by this module and requires the owner's
separate, explicit sign-off before being applied to the real entrypoint.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping


class ScopedAuthorizationError(RuntimeError):
    """A dispatch attempt fell outside its caller-declared repository scope."""


def scoped_worker_executor(
    reserved_claim: Any,
    task: Mapping[str, Any],
    *,
    packet: Mapping[str, Any],
    authorized_repositories: frozenset[str],
    inner_executor: Callable[[Any, Mapping[str, Any]], Any],
) -> Any:
    """Refuse to invoke `inner_executor` outside the authorized repository set.

    `packet` must be the same handoff packet the reserved claim's slice was
    built from -- never a second, independently-supplied repository string,
    which could drift from the task actually being dispatched (the exact
    failure class this session's earlier task-identity-mismatch bug was).
    `authorized_repositories` is a closed set the caller controls entirely;
    this function enforces it but never decides its contents.
    """
    if not isinstance(authorized_repositories, frozenset) or not all(
        isinstance(item, str) and item for item in authorized_repositories
    ):
        raise ScopedAuthorizationError("AUTHORIZED_REPOSITORY_SET_MALFORMED")
    if not isinstance(packet, Mapping):
        raise ScopedAuthorizationError("DISPATCH_HANDOFF_PACKET_MISSING")
    repository = packet.get("repository")
    if not isinstance(repository, str) or not repository:
        raise ScopedAuthorizationError("DISPATCH_HANDOFF_REPOSITORY_MISSING")
    if packet.get("slice_id") != getattr(reserved_claim, "slice_id", None):
        raise ScopedAuthorizationError("PACKET_SLICE_IDENTITY_MISMATCH")
    if not isinstance(task, Mapping) or task.get("id") != getattr(
        reserved_claim, "slice_id", None
    ):
        raise ScopedAuthorizationError("TASK_SLICE_IDENTITY_MISMATCH")
    if repository not in authorized_repositories:
        raise ScopedAuthorizationError(
            "REPOSITORY_NOT_IN_AUTHORIZED_SCOPE:" + repository
        )
    return inner_executor(reserved_claim, task)
