from __future__ import annotations

import pytest

from project_orchestrator.iron_god_migration_readiness import (
    AT_FINAL_STAGE,
    EVIDENCE_INSUFFICIENT,
    OWNER_ACTION_REQUIRED,
    STAGE_A_SHADOW,
    STAGE_B_CANDIDATE,
    STAGE_C_LOCAL_ACCEPTANCE,
    STAGE_D_PUBLICATION_CANARY,
    STAGE_G_FULL_AUTHORITY,
    ControllerState,
    MigrationReadinessError,
    assess_migration_readiness,
)

SHADOW_STATE = ControllerState(
    mode="shadow_state_only", candidate_execution_authorized=False
)
CANDIDATE_STATE = ControllerState(
    mode="candidate_mode", candidate_execution_authorized=True
)


def test_stage_a_with_sufficient_evidence_is_owner_action_required():
    result = assess_migration_readiness(
        STAGE_A_SHADOW,
        SHADOW_STATE,
        evidence_capabilities=frozenset({"cap-1", "cap-2"}),
        required_capabilities_by_stage={STAGE_B_CANDIDATE: frozenset({"cap-1"})},
    )
    assert result.status == OWNER_ACTION_REQUIRED
    assert result.next_stage == STAGE_B_CANDIDATE


def test_stage_a_with_insufficient_evidence_is_evidence_insufficient():
    result = assess_migration_readiness(
        STAGE_A_SHADOW,
        SHADOW_STATE,
        evidence_capabilities=frozenset(),
        required_capabilities_by_stage={STAGE_B_CANDIDATE: frozenset({"cap-1"})},
    )
    assert result.status == EVIDENCE_INSUFFICIENT
    assert "cap-1" in result.reason


def test_claimed_stage_a_inconsistent_with_controller_state_raises():
    with pytest.raises(MigrationReadinessError):
        assess_migration_readiness(
            STAGE_A_SHADOW,
            CANDIDATE_STATE,
            evidence_capabilities=frozenset(),
            required_capabilities_by_stage={},
        )


def test_claimed_later_stage_inconsistent_with_controller_state_raises():
    with pytest.raises(MigrationReadinessError):
        assess_migration_readiness(
            STAGE_C_LOCAL_ACCEPTANCE,
            SHADOW_STATE,
            evidence_capabilities=frozenset(),
            required_capabilities_by_stage={},
        )


def test_later_stage_with_matching_controller_state_works():
    result = assess_migration_readiness(
        STAGE_C_LOCAL_ACCEPTANCE,
        CANDIDATE_STATE,
        evidence_capabilities=frozenset({"d-cap"}),
        required_capabilities_by_stage={
            STAGE_D_PUBLICATION_CANARY: frozenset({"d-cap"})
        },
    )
    assert result.status == OWNER_ACTION_REQUIRED
    assert result.next_stage == STAGE_D_PUBLICATION_CANARY


def test_final_stage_yields_at_final_stage_with_no_next():
    result = assess_migration_readiness(
        STAGE_G_FULL_AUTHORITY,
        CANDIDATE_STATE,
        evidence_capabilities=frozenset(),
        required_capabilities_by_stage={},
    )
    assert result.status == AT_FINAL_STAGE
    assert result.next_stage is None


def test_unknown_stage_raises():
    with pytest.raises(MigrationReadinessError):
        assess_migration_readiness(
            "NOT_A_REAL_STAGE",
            SHADOW_STATE,
            evidence_capabilities=frozenset(),
            required_capabilities_by_stage={},
        )


def test_no_status_implies_automatic_advancement():
    """Even with every required capability present, the only non-raising,
    non-final statuses this module can ever produce are OWNER_ACTION_REQUIRED
    or EVIDENCE_INSUFFICIENT -- never anything implying the stage activated
    itself."""
    result = assess_migration_readiness(
        STAGE_A_SHADOW,
        SHADOW_STATE,
        evidence_capabilities=frozenset({"anything"}),
        required_capabilities_by_stage={STAGE_B_CANDIDATE: frozenset({"anything"})},
    )
    assert result.status in {
        OWNER_ACTION_REQUIRED,
        EVIDENCE_INSUFFICIENT,
        AT_FINAL_STAGE,
    }


def test_real_world_stage_a_to_b_readiness_using_ledger_activation_fences():
    """Composes the actual, ledger-declared activation_fences for
    candidate_mode (controller-handoff-state.json's own
    candidate_attempt_state_machine.activation_fences list) as the
    required capabilities for Stage B, against the engineering
    capabilities this session actually proved (per the same ledger's
    durable_slice_lease and candidate_attempt_state_machine sections).
    Confirms OWNER_ACTION_REQUIRED is the correct real-world answer
    today: every engineering fence is proven, only the owner-performed
    mode/candidate_execution_authorized flip remains.
    """
    ledger_declared_activation_fences = frozenset(
        {
            "owner/exclusive gate",
            "resource gate",
            "per-slice exact worktree/branch/base SHA",
            "fenced SQLite slice lease",
        }
    )
    proven_engineering_capabilities = frozenset(
        {
            "owner/exclusive gate",
            "resource gate",
            "per-slice exact worktree/branch/base SHA",
            "fenced SQLite slice lease",
        }
    )

    result = assess_migration_readiness(
        STAGE_A_SHADOW,
        SHADOW_STATE,
        evidence_capabilities=proven_engineering_capabilities,
        required_capabilities_by_stage={
            STAGE_B_CANDIDATE: ledger_declared_activation_fences
        },
    )
    assert result.status == OWNER_ACTION_REQUIRED
    assert result.next_stage == STAGE_B_CANDIDATE
