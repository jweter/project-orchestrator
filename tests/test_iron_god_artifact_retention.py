from __future__ import annotations

import pytest

from project_orchestrator.iron_god_artifact_retention import (
    ArtifactEntry,
    UnknownCheckpointClassError,
    UnknownPressureClassificationError,
    decide_artifact_retention,
)
from project_orchestrator.local_storage_pressure import (
    BLOCKED,
    HEALTHY,
    STORAGE_PRESSURE,
)


def _entry(**overrides) -> ArtifactEntry:
    defaults = dict(artifact_id="artifact-001", checkpoint_class=1, in_active_use=False)
    defaults.update(overrides)
    return ArtifactEntry(**defaults)


def test_unknown_checkpoint_class_raises():
    with pytest.raises(UnknownCheckpointClassError):
        decide_artifact_retention(
            _entry(checkpoint_class=99), pressure=STORAGE_PRESSURE
        )


def test_unknown_pressure_raises():
    with pytest.raises(UnknownPressureClassificationError):
        decide_artifact_retention(_entry(), pressure="WEIRD")


@pytest.mark.parametrize("checkpoint_class", [2, 3, 4, 5])
@pytest.mark.parametrize("pressure", [HEALTHY, STORAGE_PRESSURE, BLOCKED])
def test_protected_classes_never_eligible(checkpoint_class, pressure):
    decision = decide_artifact_retention(
        _entry(checkpoint_class=checkpoint_class, in_active_use=False),
        pressure=pressure,
    )
    assert decision.eligible_for_reclamation is False


def test_class_one_never_eligible_when_healthy():
    decision = decide_artifact_retention(
        _entry(checkpoint_class=1, in_active_use=False),
        pressure=HEALTHY,
    )
    assert decision.eligible_for_reclamation is False


@pytest.mark.parametrize("pressure", [HEALTHY, STORAGE_PRESSURE, BLOCKED])
def test_class_one_never_eligible_when_in_active_use(pressure):
    decision = decide_artifact_retention(
        _entry(checkpoint_class=1, in_active_use=True),
        pressure=pressure,
    )
    assert decision.eligible_for_reclamation is False


@pytest.mark.parametrize("pressure", [STORAGE_PRESSURE, BLOCKED])
def test_class_one_idle_eligible_under_pressure(pressure):
    decision = decide_artifact_retention(
        _entry(checkpoint_class=1, in_active_use=False),
        pressure=pressure,
    )
    assert decision.eligible_for_reclamation is True
