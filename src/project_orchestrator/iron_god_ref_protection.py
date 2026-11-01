"""Ref protection policy for all refs/iron-god/* namespaces.

This module is a pure decision function: given a ref name and a proposed
operation, it answers whether that operation is forbidden under the Iron
God ref protection policy. It performs no Git operation itself -- it is a
second, independent policy layer on top of each ref module's own internal
safeguards (iron_god_refs.py, iron_god_candidate_refs.py,
iron_god_milestone_refs.py, iron_god_recovery_refs.py), meant to be
consulted by any code path about to touch a ref, never a replacement for
those modules' own compare-and-swap writes.

This module is not wired to any automatic or unattended call site.
"""

from __future__ import annotations

from dataclasses import dataclass

ACCEPTED_PREFIX = "refs/iron-god/accepted/"
CANDIDATE_PREFIX = "refs/iron-god/candidate/"
MILESTONE_PREFIX = "refs/iron-god/milestone/"
RECOVERY_PREFIX = "refs/iron-god/recovery/"
BASELINE_PREFIX = "refs/iron-god/baseline/"  # the cumulative accepted baseline (#597); advance-only, never deleted

PROTECTED_PREFIXES = (
    ACCEPTED_PREFIX,
    CANDIDATE_PREFIX,
    MILESTONE_PREFIX,
    RECOVERY_PREFIX,
    BASELINE_PREFIX,
)

# Refs with no sanctioned delete path in their owning module: accepted
# (advance-only), milestone and recovery (permanent). Only candidate refs
# expose a real clear function (iron_god_candidate_refs.clear_candidate).
NO_DELETE_PREFIXES = (
    ACCEPTED_PREFIX,
    MILESTONE_PREFIX,
    RECOVERY_PREFIX,
    BASELINE_PREFIX,
)

FORCE_OPERATIONS = frozenset({"force-update", "force-delete", "force-push"})
DELETE_OPERATIONS = frozenset({"delete", "force-delete"})


class RefProtectionViolation(RuntimeError):
    """Raised by raise_if_forbidden when a proposed operation is disallowed."""


def is_protected_ref(ref_name: str) -> bool:
    """True if `ref_name` falls under any refs/iron-god/* namespace."""
    return any(ref_name.startswith(prefix) for prefix in PROTECTED_PREFIXES)


def is_no_delete_ref(ref_name: str) -> bool:
    """True if `ref_name` has no sanctioned delete path in its owning module."""
    return any(ref_name.startswith(prefix) for prefix in NO_DELETE_PREFIXES)


@dataclass(frozen=True)
class RefOperationDecision:
    ref_name: str
    operation: str
    allowed: bool
    reason: str


def check_ref_operation(ref_name: str, operation: str) -> RefOperationDecision:
    """Classify whether `operation` may proceed against `ref_name`.

    This never performs the operation -- it only decides. An "allowed"
    result still means the caller must use the owning ref module's own
    compare-and-swap function, never a bare git write.
    """
    if not is_protected_ref(ref_name):
        return RefOperationDecision(
            ref_name=ref_name,
            operation=operation,
            allowed=True,
            reason="ref is outside all iron-god protected namespaces",
        )

    if operation in FORCE_OPERATIONS:
        return RefOperationDecision(
            ref_name=ref_name,
            operation=operation,
            allowed=False,
            reason="force operations are never permitted on a protected iron-god ref",
        )

    if operation in DELETE_OPERATIONS and is_no_delete_ref(ref_name):
        return RefOperationDecision(
            ref_name=ref_name,
            operation=operation,
            allowed=False,
            reason="this ref's owning module exposes no delete path; deletion is never permitted",
        )

    if operation in DELETE_OPERATIONS:
        return RefOperationDecision(
            ref_name=ref_name,
            operation=operation,
            allowed=True,
            reason=(
                "deletion is only permitted via the owning module's own "
                "compare-and-swap clear function, never a bare delete"
            ),
        )

    return RefOperationDecision(
        ref_name=ref_name,
        operation=operation,
        allowed=True,
        reason=(
            "must still go through the owning ref module's compare-and-swap "
            "function, never a bare write"
        ),
    )


def raise_if_forbidden(ref_name: str, operation: str) -> None:
    """Raise RefProtectionViolation if `operation` on `ref_name` is disallowed."""
    decision = check_ref_operation(ref_name, operation)
    if not decision.allowed:
        raise RefProtectionViolation(
            f"{operation!r} on {ref_name!r} is forbidden: {decision.reason}"
        )
