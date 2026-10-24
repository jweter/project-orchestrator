"""Migration readiness assessment (roadmap Phase 10: Controlled
Migration).

Phase 10's seven stages (A Shadow Mode -> B Candidate Mode -> C Local
Acceptance Mode -> D Publication Queue Canary -> E Multi-Repository
Rollout -> F Retire GitHub-Coupled Worker Behavior -> G Full Iron God
Authority) each activate more real scheduling/execution authority
than the last. This module never activates a stage itself -- it is a
pure, read-only decision function answering whether evidence
supports entering the next stage, given the claimed current stage,
the real controller state (mode / candidate_execution_authorized),
and a caller-supplied mapping of which capabilities each stage
requires. Advancing past Stage A always requires an owner-performed
authority change (flipping candidate_execution_authorized and/or
mode) -- the same boundary respected everywhere else this session
("Security Weaken" denial, never retried). Even when evidence is
fully sufficient, the result is always OWNER_ACTION_REQUIRED, never
an automatic-advancement status. This module performs no file or
state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass

STAGE_A_SHADOW = "A_SHADOW_MODE"
STAGE_B_CANDIDATE = "B_CANDIDATE_MODE"
STAGE_C_LOCAL_ACCEPTANCE = "C_LOCAL_ACCEPTANCE_MODE"
STAGE_D_PUBLICATION_CANARY = "D_PUBLICATION_QUEUE_CANARY"
STAGE_E_MULTI_REPO = "E_MULTI_REPOSITORY_ROLLOUT"
STAGE_F_RETIRE_GITHUB_COUPLED = "F_RETIRE_GITHUB_COUPLED_WORKER_BEHAVIOR"
STAGE_G_FULL_AUTHORITY = "G_FULL_IRON_GOD_AUTHORITY"

STAGE_ORDER = (
    STAGE_A_SHADOW,
    STAGE_B_CANDIDATE,
    STAGE_C_LOCAL_ACCEPTANCE,
    STAGE_D_PUBLICATION_CANARY,
    STAGE_E_MULTI_REPO,
    STAGE_F_RETIRE_GITHUB_COUPLED,
    STAGE_G_FULL_AUTHORITY,
)

OWNER_ACTION_REQUIRED = "OWNER_ACTION_REQUIRED"
EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
AT_FINAL_STAGE = "AT_FINAL_STAGE"


class MigrationReadinessError(RuntimeError):
    """Raised when the claimed stage is unknown, or is inconsistent
    with the real controller state."""


@dataclass(frozen=True)
class ControllerState:
    mode: str
    candidate_execution_authorized: bool


@dataclass(frozen=True)
class ReadinessAssessment:
    current_stage: str
    next_stage: str | None
    status: str
    reason: str


def _assert_controller_state_matches_stage(
    current_stage: str, controller_state: ControllerState
) -> None:
    if current_stage == STAGE_A_SHADOW:
        if (
            controller_state.mode != "shadow_state_only"
            or controller_state.candidate_execution_authorized
        ):
            raise MigrationReadinessError(
                "current_stage is Shadow Mode but controller_state does not "
                "match (mode must be 'shadow_state_only' and "
                "candidate_execution_authorized must be False)"
            )
        return

    if (
        controller_state.mode != "candidate_mode"
        or not controller_state.candidate_execution_authorized
    ):
        raise MigrationReadinessError(
            f"current_stage is {current_stage} but controller_state shows no "
            "authorized candidate execution (mode must be 'candidate_mode' "
            "and candidate_execution_authorized must be True)"
        )


def assess_migration_readiness(
    current_stage: str,
    controller_state: ControllerState,
    evidence_capabilities: frozenset[str],
    *,
    required_capabilities_by_stage: dict[str, frozenset[str]],
) -> ReadinessAssessment:
    """Decide whether evidence supports entering the stage after
    `current_stage`.

    Raises MigrationReadinessError if `current_stage` is unrecognized,
    is inconsistent with `controller_state`, or if the next stage has
    no explicit, non-empty entry in `required_capabilities_by_stage` --
    readiness is never inferred from a missing or empty policy. Never
    returns a status implying automatic advancement --
    OWNER_ACTION_REQUIRED still means a human must perform the
    authority change.
    """
    if current_stage not in STAGE_ORDER:
        raise MigrationReadinessError(f"unknown stage: {current_stage!r}")

    _assert_controller_state_matches_stage(current_stage, controller_state)

    stage_index = STAGE_ORDER.index(current_stage)
    if stage_index == len(STAGE_ORDER) - 1:
        return ReadinessAssessment(
            current_stage=current_stage,
            next_stage=None,
            status=AT_FINAL_STAGE,
            reason="already at Stage G: Full Iron God Authority",
        )

    next_stage = STAGE_ORDER[stage_index + 1]
    required = required_capabilities_by_stage.get(next_stage)
    if not required:
        raise MigrationReadinessError(
            f"no explicit, non-empty required-capabilities policy is declared "
            f"for {next_stage}; readiness must never be inferred from a "
            "missing or empty policy"
        )
    missing = required - evidence_capabilities

    if missing:
        return ReadinessAssessment(
            current_stage=current_stage,
            next_stage=next_stage,
            status=EVIDENCE_INSUFFICIENT,
            reason=f"missing evidence for {next_stage}: {sorted(missing)}",
        )

    return ReadinessAssessment(
        current_stage=current_stage,
        next_stage=next_stage,
        status=OWNER_ACTION_REQUIRED,
        reason=(
            f"evidence supports entering {next_stage}, but activation always "
            "requires an owner-performed authority change; this function "
            "never performs it"
        ),
    )
