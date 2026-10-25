import json
from pathlib import Path

import pytest

from project_orchestrator import execution_authority as authority

MODULE_SOURCE = Path(authority.__file__).read_text(encoding="utf-8")


def controller(tmp_path, **overrides):
    payload = {
        "schema_version": 1,
        "controller": "iron-god-handoff-v1",
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
    }
    payload.update(overrides)
    path = tmp_path / "controller-handoff-state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def released():
    return authority.OWNER_RELEASED


def test_live_shadow_controller_is_denied_even_with_released_owner(tmp_path):
    path = controller(tmp_path, mode="shadow_state_only", candidate_execution_authorized=False)
    decision = authority.evaluate_execution_authority(path, released)
    assert decision.permitted is False
    assert decision.reasons == (
        "CONTROLLER_SHADOW_MODE",
        "CONTROLLER_CANDIDATE_EXECUTION_DISABLED",
    )


def test_authorized_controller_without_owner_contract_is_denied(tmp_path):
    decision = authority.evaluate_execution_authority(controller(tmp_path))
    assert decision.permitted is False
    assert decision.reasons == (authority.OWNER_AUTHORITY_CONTRACT_UNDEFINED,)


def test_both_independent_inputs_are_required_to_permit(tmp_path):
    decision = authority.evaluate_execution_authority(controller(tmp_path), released)
    assert decision == authority.ExecutionDecision(permitted=True, reasons=())


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"controller": "something-else"}, "CONTROLLER_IDENTITY_UNVERIFIED"),
        ({"mode": "full_power"}, "CONTROLLER_MODE_UNSUPPORTED"),
        ({"candidate_execution_authorized": "true"}, "CONTROLLER_CANDIDATE_EXECUTION_DISABLED"),
        ({"candidate_execution_authorized": 1}, "CONTROLLER_CANDIDATE_EXECUTION_DISABLED"),
    ],
)
def test_controller_drift_fails_closed(tmp_path, overrides, reason):
    decision = authority.evaluate_execution_authority(controller(tmp_path, **overrides), released)
    assert decision.permitted is False
    assert reason in decision.reasons


def test_missing_malformed_and_unavailable_controller_state_fail_closed(tmp_path, monkeypatch):
    assert authority.evaluate_execution_authority(tmp_path / "absent.json", released).reasons == (
        "CONTROLLER_STATE_MISSING_OR_UNTRUSTED",
    )
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert authority.evaluate_execution_authority(bad, released).reasons == (
        "CONTROLLER_STATE_UNREADABLE",
    )
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert authority.evaluate_execution_authority(None, released).reasons == (
        "CONTROLLER_STATE_PATH_UNAVAILABLE",
    )


@pytest.mark.parametrize("owner", ["", None, "AUTHORIZED", "VERIFIED_HELD"])
def test_any_owner_attestation_but_verified_released_denies(tmp_path, owner):
    decision = authority.evaluate_execution_authority(controller(tmp_path), lambda: owner)
    assert decision.permitted is False


def test_owner_provider_failure_is_unverified_authority(tmp_path):
    def broken():
        raise OSError("provider unavailable")

    decision = authority.evaluate_execution_authority(controller(tmp_path), broken)
    assert decision.reasons == ("OWNER_AUTHORITY_UNVERIFIED",)


def test_default_path_resolves_controller_under_localappdata(tmp_path, monkeypatch):
    root = tmp_path / "ExponentialEngine" / "IronGodLocal"
    root.mkdir(parents=True)
    controller(root)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert authority.evaluate_execution_authority(owner_attestation=released).permitted is True


def test_retired_owner_marker_is_never_an_authority_input(tmp_path, monkeypatch):
    """A released retired marker next to an authorized controller changes nothing."""
    flow = tmp_path / "ExponentialEngine" / "flow-state"
    flow.mkdir(parents=True)
    (flow / "human-mode.json").write_text('{"enabled": false}', encoding="utf-8")
    (flow / "iron-god-exclusive.json").write_text('{"enabled": true}', encoding="utf-8")
    root = tmp_path / "ExponentialEngine" / "IronGodLocal"
    root.mkdir(parents=True)
    controller(root)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    decision = authority.evaluate_execution_authority()
    assert decision.reasons == (authority.OWNER_AUTHORITY_CONTRACT_UNDEFINED,)
    assert "human-mode.json" not in MODULE_SOURCE
    assert "iron-god-exclusive" not in MODULE_SOURCE
    assert "human_ownership" not in MODULE_SOURCE


def test_decision_never_writes_controller_state(tmp_path):
    path = controller(tmp_path, mode="shadow_state_only", candidate_execution_authorized=False)
    before = path.read_bytes()
    authority.evaluate_execution_authority(path, released)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]
