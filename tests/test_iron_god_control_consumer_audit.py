"""Read-only acceptance of the deployed desktop-control source audit."""

from __future__ import annotations

import hashlib
import json

import pytest

from project_orchestrator.iron_god_control_consumer_audit import (
    CONTROL_CONSUMERS,
    _load_controller_state,
    audit_control_consumers,
    main,
)


def make_scripts(root, legacy=None):
    for name in CONTROL_CONSUMERS:
        path = root.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        contents = "REM safe script source\n"
        if name == legacy:
            contents += 'marker = "HUMAN-MODE.JSON"\n'
        path.write_text(contents, encoding="utf-8")
    return root


def test_deployed_consumer_reference_identifies_exact_source_line(tmp_path):
    root = make_scripts(tmp_path, legacy="DesktopControlSilent.vbs")
    result = audit_control_consumers(
        root,
        controller={
            "mode": "shadow_state_only",
            "candidate_execution_authorized": False,
        },
    )
    assert result.status == "BLOCKED"
    assert result.execution_authorized is False
    consumer = result.consumers[0]
    assert consumer.relative_path == "DesktopControlSilent.vbs"
    assert consumer.legacy_reference_lines == (2,)
    assert consumer.problem is None
    assert (
        consumer.source_sha256
        == hashlib.sha256((root / "DesktopControlSilent.vbs").read_bytes()).hexdigest()
    )
    assert "DesktopControlSilent.vbs:RETIRED_CONTROL_REFERENCE:2" in result.blockers
    assert "CONTROLLER_CANDIDATE_EXECUTION_DISABLED" in result.blockers


def test_no_retired_reference_does_not_authorize_a_cutover(tmp_path):
    root = make_scripts(tmp_path)
    result = audit_control_consumers(
        root,
        controller={"mode": "candidate_mode", "candidate_execution_authorized": True},
    )
    assert result.status == "SOURCE_CONSUMERS_CLEAN_UNCOMMISSIONED"
    assert result.execution_authorized is False
    assert "CURRENT_OWNER_CONTROL_AND_SUPERVISOR_ACK_UNVERIFIED" in result.blockers
    assert all(
        not c.legacy_reference_lines and c.problem is None for c in result.consumers
    )


def test_missing_script_fails_closed(tmp_path):
    root = make_scripts(tmp_path)
    (root / "Take-The-Wheel.ps1").unlink()
    result = audit_control_consumers(root, controller={})
    assert "Take-The-Wheel.ps1:SOURCE_MISSING_OR_LINKED" in result.blockers
    assert result.execution_authorized is False


def test_malformed_controller_never_converts_to_permission(tmp_path):
    root = make_scripts(tmp_path)
    for state in (
        None,
        [],
        {"mode": "candidate_mode", "candidate_execution_authorized": 1},
    ):
        result = audit_control_consumers(root, controller=state)
        assert result.status == "BLOCKED"
        assert result.execution_authorized is False


def test_file_source_observation_does_not_read_owner_state_file(tmp_path, monkeypatch):
    root = make_scripts(tmp_path, legacy="Watch-ContinuousFlow.ps1")
    original = type(root).read_bytes
    visited = []

    def checked_read(path):
        visited.append(path.name)
        assert path.name.casefold() != "human-mode.json"
        return original(path)

    monkeypatch.setattr(type(root), "read_bytes", checked_read)
    result = audit_control_consumers(root, controller={"mode": "shadow_state_only"})
    assert "Watch-ContinuousFlow.ps1:RETIRED_CONTROL_REFERENCE:2" in result.blockers
    assert len(visited) == len(CONTROL_CONSUMERS)


def test_cli_writes_only_stdout_and_preserves_files(tmp_path, capsys):
    root = make_scripts(tmp_path / "engine", legacy="ModeController/ModeCore.ps1")
    controller = tmp_path / "controller-handoff-state.json"
    controller.write_text(
        json.dumps(
            {"mode": "shadow_state_only", "candidate_execution_authorized": False}
        ),
        encoding="utf-8",
    )
    before = sorted(
        (str(p), p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()
    )
    rc = main(["--engine-root", str(root), "--controller-state", str(controller)])
    report = json.loads(capsys.readouterr().out)
    after = sorted((str(p), p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file())
    assert rc == 3
    assert report["status"] == "BLOCKED"
    assert (
        "ModeController/ModeCore.ps1:RETIRED_CONTROL_REFERENCE:2" in report["blockers"]
    )
    assert report["execution_authorized"] is False
    assert before == after


def test_cli_rejects_direct_legacy_path_without_reading_it(tmp_path):
    with pytest.raises(ValueError, match="CONTROLLER_PATH_NOT_AUTHORIZED"):
        _load_controller_state(tmp_path / "human-mode.json")


def test_bad_controller_json_is_blocked_without_fallback(tmp_path, capsys):
    root = make_scripts(tmp_path / "engine")
    controller = tmp_path / "controller-handoff-state.json"
    controller.write_text("{", encoding="utf-8")
    assert (
        main(["--engine-root", str(root), "--controller-state", str(controller)]) == 3
    )
    report = json.loads(capsys.readouterr().out)
    assert "CONTROLLER_STATE_UNREADABLE" in report["blockers"]


def test_source_size_cap_fails_closed(tmp_path):
    root = make_scripts(tmp_path)
    from project_orchestrator.iron_god_control_consumer_audit import MAX_SCRIPT_BYTES

    (root / "Take-The-Wheel.ps1").write_bytes(b"a" * (MAX_SCRIPT_BYTES + 1))
    result = audit_control_consumers(root, controller={})
    assert "Take-The-Wheel.ps1:SOURCE_OVERSIZE" in result.blockers


def test_remote_transport_stop_path_detected_independently_of_marker(tmp_path):
    root = make_scripts(tmp_path)
    (root / "Enter-HumanMode.ps1").write_text(
        "Get-Process foo\n"
        "Stop-ScheduledTask -TaskName 'Azathoth-Remote-Desktop-Commander'\n"
        "Disable-ScheduledTask -TaskName 'Azathoth-Remote-Desktop-Commander'\n"
        "Stop-Process -Id $r.ProcessId -Force\n",
        encoding="utf-8",
    )
    result = audit_control_consumers(
        root,
        controller={"mode": "candidate_mode", "candidate_execution_authorized": True},
    )
    assert "Enter-HumanMode.ps1:REMOTE_TRANSPORT_STOP_PATH:2,3,4" in result.blockers
    assert result.status == "BLOCKED"
    assert result.execution_authorized is False


def test_comment_only_remote_transport_reference_is_not_a_stop_path(tmp_path):
    root = make_scripts(tmp_path)
    (root / "Enter-HumanMode.ps1").write_text(
        "# Stop-ScheduledTask -TaskName 'Azathoth-Remote-Desktop-Commander'\n",
        encoding="utf-8",
    )
    result = audit_control_consumers(
        root,
        controller={"mode": "candidate_mode", "candidate_execution_authorized": True},
    )
    assert not any("REMOTE_TRANSPORT_STOP_PATH" in x for x in result.blockers)
    assert result.execution_authorized is False
