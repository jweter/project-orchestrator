import os
from pathlib import Path

import pytest

from project_orchestrator import execution_authority as authority
from project_orchestrator import iron_god_owner_control as oc
from tests.owner_authority import TEST_RELEASE_SHA, grant_owner_authority, revoke_owner_authority

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI owner control")

MODULE_SOURCE = Path(authority.__file__).read_text(encoding="utf-8")


def flow(tmp_path):
    path = tmp_path / "ExponentialEngine" / "flow-state"
    path.mkdir(parents=True)
    return path


def test_no_owner_controller_state_denies(tmp_path):
    decision = authority.evaluate_execution_authority(
        oc.control_root(flow(tmp_path)), release_sha=TEST_RELEASE_SHA
    )
    assert decision.permitted is False
    assert decision.reasons == ("OWNER_NOT_ENROLLED",)


def test_live_acknowledged_grant_permits_its_release_only(tmp_path):
    state = flow(tmp_path)
    supervisor = grant_owner_authority(state)
    root = oc.control_root(state)
    worker = authority.evaluate_execution_authority(root, release_sha=TEST_RELEASE_SHA)
    assert worker.permitted is True and worker.supervisor_instance_id == supervisor.instance_id
    assert authority.evaluate_execution_authority(root, release_sha=TEST_RELEASE_SHA, supervisor=supervisor).permitted
    other = authority.evaluate_execution_authority(root, release_sha="b" * 40)
    assert other.permitted is False and other.reasons == ("GRANT_RELEASE_MISMATCH",)


def test_unpinned_caller_is_denied(tmp_path):
    state = flow(tmp_path)
    grant_owner_authority(state)
    for release in (None, ""):
        assert authority.evaluate_execution_authority(
            oc.control_root(state), release_sha=release
        ).reasons == (authority.OWNER_RELEASE_UNPINNED,)


def test_pending_grant_is_not_execution_authority(tmp_path):
    state = flow(tmp_path)
    root = oc.control_root(state)
    oc.issue_command(root, oc.TAKE_THE_WHEEL, release_sha=TEST_RELEASE_SHA,
                     origin_check=lambda: {"session_id": 1, "origin": "desktop-control"})
    decision = authority.evaluate_execution_authority(root, release_sha=TEST_RELEASE_SHA)
    assert decision.permitted is False
    assert decision.reasons == ("SUPERVISOR_ACK_PENDING",)


def test_supervisor_path_acknowledges_then_permits(tmp_path):
    state = flow(tmp_path)
    root = oc.control_root(state)
    oc.issue_command(root, oc.TAKE_THE_WHEEL, release_sha=TEST_RELEASE_SHA,
                     origin_check=lambda: {"session_id": 1, "origin": "desktop-control"})
    me = oc.current_supervisor_identity("s" * 32)
    decision = authority.acknowledge_and_evaluate(root, me, release_sha=TEST_RELEASE_SHA)
    assert decision.permitted is True and decision.supervisor_instance_id == me.instance_id


def test_revocation_denies_immediately(tmp_path):
    state = flow(tmp_path)
    grant_owner_authority(state)
    revoke_owner_authority(state)
    decision = authority.evaluate_execution_authority(oc.control_root(state), release_sha=TEST_RELEASE_SHA)
    assert decision.permitted is False and decision.reasons == ("OWNER_CONTROL_ACTIVE",)


def test_dead_acknowledging_supervisor_denies(tmp_path):
    state = flow(tmp_path)
    grant_owner_authority(state)
    decision = authority.evaluate_execution_authority(
        oc.control_root(state), release_sha=TEST_RELEASE_SHA, liveness=lambda _identity: False
    )
    assert decision.reasons == ("ACKNOWLEDGING_SUPERVISOR_GONE",)


def test_default_root_resolves_under_localappdata(tmp_path, monkeypatch):
    state = flow(tmp_path)
    grant_owner_authority(state)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert authority.evaluate_execution_authority(release_sha=TEST_RELEASE_SHA).permitted is True
    monkeypatch.delenv("LOCALAPPDATA")
    assert authority.evaluate_execution_authority(release_sha=TEST_RELEASE_SHA).reasons == (
        "EXECUTION_AUTHORITY_UNVERIFIED",
    )


def test_legacy_controller_flag_and_retired_markers_are_not_inputs(tmp_path, monkeypatch):
    """An authorizing legacy handoff flag plus released retired markers grant nothing."""
    state = flow(tmp_path)
    (state / "human-mode.json").write_text('{"enabled": false}', encoding="utf-8")
    (state / "iron-god-exclusive.json").write_text('{"enabled": true}', encoding="utf-8")
    legacy = tmp_path / "ExponentialEngine" / "IronGodLocal"
    legacy.mkdir()
    (legacy / "controller-handoff-state.json").write_text(
        '{"controller":"iron-god-handoff-v1","mode":"candidate_mode","candidate_execution_authorized":true}',
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert authority.evaluate_execution_authority(release_sha=TEST_RELEASE_SHA).permitted is False
    for retired in ("human-mode.json", "iron-god-exclusive", "controller-handoff-state", "human_ownership"):
        assert retired not in MODULE_SOURCE.split('"""', 2)[2]


def test_decision_never_writes_controller_state(tmp_path):
    state = flow(tmp_path)
    grant_owner_authority(state)
    root = oc.control_root(state)
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    authority.evaluate_execution_authority(root, release_sha=TEST_RELEASE_SHA)
    after = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    assert before == after
