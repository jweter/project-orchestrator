from __future__ import annotations

import pytest

from project_orchestrator.iron_god_ref_protection import (
    RefProtectionViolation,
    check_ref_operation,
    is_no_delete_ref,
    is_protected_ref,
    raise_if_forbidden,
)

PROTECTED_EXAMPLES = (
    "refs/iron-god/accepted/demo",
    "refs/iron-god/candidate/IG-099",
    "refs/iron-god/milestone/M-001",
    "refs/iron-god/recovery/INC-001",
)

NO_DELETE_EXAMPLES = (
    "refs/iron-god/accepted/demo",
    "refs/iron-god/milestone/M-001",
    "refs/iron-god/recovery/INC-001",
)


@pytest.mark.parametrize("ref_name", PROTECTED_EXAMPLES)
def test_is_protected_ref_true_for_all_namespaces(ref_name):
    assert is_protected_ref(ref_name) is True


def test_is_protected_ref_false_for_unrelated_ref():
    assert is_protected_ref("refs/heads/main") is False
    assert is_protected_ref("refs/tags/v1.0.0") is False


@pytest.mark.parametrize("ref_name", NO_DELETE_EXAMPLES)
def test_is_no_delete_ref_true_for_accepted_milestone_recovery(ref_name):
    assert is_no_delete_ref(ref_name) is True


def test_is_no_delete_ref_false_for_candidate():
    assert is_no_delete_ref("refs/iron-god/candidate/IG-099") is False


@pytest.mark.parametrize("ref_name", PROTECTED_EXAMPLES)
@pytest.mark.parametrize("operation", ["force-update", "force-delete", "force-push"])
def test_force_operations_always_disallowed_on_protected_refs(ref_name, operation):
    decision = check_ref_operation(ref_name, operation)
    assert decision.allowed is False


@pytest.mark.parametrize("ref_name", NO_DELETE_EXAMPLES)
def test_delete_disallowed_on_accepted_milestone_recovery(ref_name):
    decision = check_ref_operation(ref_name, "delete")
    assert decision.allowed is False


def test_delete_allowed_on_candidate_ref():
    decision = check_ref_operation("refs/iron-god/candidate/IG-099", "delete")
    assert decision.allowed is True
    assert "compare-and-swap" in decision.reason


@pytest.mark.parametrize("ref_name", PROTECTED_EXAMPLES)
def test_plain_update_allowed_but_directs_to_owning_module(ref_name):
    decision = check_ref_operation(ref_name, "update")
    assert decision.allowed is True
    assert "compare-and-swap" in decision.reason


def test_unprotected_ref_always_allowed_regardless_of_operation():
    for operation in ("update", "delete", "force-update", "force-delete", "force-push"):
        decision = check_ref_operation("refs/heads/feature/x", operation)
        assert decision.allowed is True


def test_raise_if_forbidden_raises_on_disallowed_operation():
    with pytest.raises(RefProtectionViolation):
        raise_if_forbidden("refs/iron-god/milestone/M-001", "delete")


def test_raise_if_forbidden_does_not_raise_on_allowed_operation():
    raise_if_forbidden("refs/iron-god/candidate/IG-099", "delete")
    raise_if_forbidden("refs/heads/main", "force-delete")
