"""The desktop readiness audit never changes owner or controller state."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from project_orchestrator.iron_god_desktop_readiness import (
    SCRIPT_PATHS,
    audit_desktop_control,
    main,
)

SHA = "d9d9328deaf2977e0570b4dba1f12bd8c7fb1317"
NOW = datetime(2026, 10, 22, 22, 0, tzinfo=UTC)


def fixture_install(
    root: Path, *, legacy_script: str | None = None
) -> tuple[Path, Path]:
    base = root / "ExponentialEngine"
    state = base / "flow-state"
    state.mkdir(parents=True)
    for label, relative in SCRIPT_PATHS.items():
        script = base / relative
        script.parent.mkdir(parents=True, exist_ok=True)
        text = "read-only test fixture"
        if label == legacy_script:
            text += " human-mode.json"
        script.write_text(text, encoding="utf-8")

    controller = base / "IronGodLocal" / "controller-handoff-state.json"
    controller.parent.mkdir(parents=True)
    records = {
        state / "iron-god-desktop-mode.json": {
            "acknowledged_by_engine": True,
            "effective_remote_authorization": "ADVISORY",
        },
        state / "supervisor-generation-heartbeat.json": {
            "status": "RUNNING_CURRENT",
            "actor_revision": SHA,
            "heartbeat_at_utc": NOW.isoformat(),
        },
        state / "supervisor-pinned-release.json": {"accepted_sha": SHA},
        controller: {"mode": "candidate_mode", "candidate_execution_authorized": True},
    }
    for path, payload in records.items():
        path.write_text(json.dumps(payload), encoding="utf-8")
    return base, controller


def test_even_all_good_advisory_files_cannot_authorize_owner_control(tmp_path):
    base, controller = fixture_install(tmp_path)
    result = audit_desktop_control(base=base, controller_file=controller, now=NOW)
    assert result["status"] == "OWNER_CONTROL_UNVERIFIED"
    assert result["reasons"] == ["AUTHORITATIVE_OWNER_HANDOFF_UNVERIFIED"]
    assert result["transition_performed"] is False
    assert result["candidate_execution_authorized_by_audit"] is False
    assert result["shortcut_click_test_performed"] is False


@pytest.mark.parametrize("script", tuple(SCRIPT_PATHS))
def test_detects_retired_static_reference_in_every_consumer(tmp_path, script):
    base, controller = fixture_install(tmp_path, legacy_script=script)
    report = audit_desktop_control(base=base, controller_file=controller, now=NOW)
    assert "RETIRED_REFERENCE:" + script in report["reasons"]


def test_shadow_mode_and_unacknowledged_request_never_count_as_ready(tmp_path):
    base, controller = fixture_install(tmp_path)
    state = base / "flow-state"
    (state / "iron-god-desktop-mode.json").write_text(
        json.dumps(
            {
                "acknowledged_by_engine": False,
                "effective_remote_authorization": "NONE",
                "owner_status_at_selection": "RELEASED",
            }
        ),
        encoding="utf-8",
    )
    controller.write_text(
        json.dumps(
            {"mode": "shadow_state_only", "candidate_execution_authorized": False}
        ),
        encoding="utf-8",
    )
    reasons = audit_desktop_control(base=base, controller_file=controller, now=NOW)[
        "reasons"
    ]
    assert "CONTROLLER_SHADOW_MODE" in reasons
    assert "CANDIDATE_EXECUTION_NOT_AUTHORIZED" in reasons
    assert "ENGINE_ACKNOWLEDGEMENT_NOT_PROVEN" in reasons
    assert "EFFECTIVE_REMOTE_AUTHORIZATION_NOT_PROVEN" in reasons
    assert "AUTHORITATIVE_OWNER_HANDOFF_UNVERIFIED" in reasons


def test_stale_and_mismatched_supervisor_evidence_is_explicit(tmp_path):
    base, controller = fixture_install(tmp_path)
    state = base / "flow-state"
    (state / "supervisor-generation-heartbeat.json").write_text(
        json.dumps(
            {
                "status": "RUNNING_CURRENT",
                "actor_revision": "f" * 40,
                "heartbeat_at_utc": (NOW - timedelta(minutes=10)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    reasons = audit_desktop_control(base=base, controller_file=controller, now=NOW)[
        "reasons"
    ]
    assert "PINNED_SUPERVISOR_REVISION_UNVERIFIED" in reasons
    assert "FRESH_SUPERVISOR_HEARTBEAT_UNVERIFIED" in reasons


def test_missing_and_invalid_evidence_fail_closed(tmp_path):
    base, controller = fixture_install(tmp_path)
    (base / "flow-state" / "iron-god-desktop-mode.json").write_text(
        "not json", encoding="utf-8"
    )
    (base / "flow-state" / "supervisor-pinned-release.json").unlink()
    (base / "ModeController" / "ModeCore.ps1").unlink()
    reasons = audit_desktop_control(base=base, controller_file=controller, now=NOW)[
        "reasons"
    ]
    assert "MODE_UNREADABLE" in reasons
    assert "PIN_UNREADABLE" in reasons
    assert "SCRIPT_UNREADABLE:mode_core" in reasons


def test_audit_never_reads_a_retired_control_record(tmp_path, monkeypatch):
    base, controller = fixture_install(tmp_path)
    original = Path.read_text

    def guarded_read(self, *args, **kwargs):
        if self.name == "human-mode.json":
            raise AssertionError("retired owner record must never be read")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    report = audit_desktop_control(base=base, controller_file=controller, now=NOW)
    assert report["status"] == "OWNER_CONTROL_UNVERIFIED"


def test_audit_does_not_modify_disk_state(tmp_path):
    base, controller = fixture_install(tmp_path)
    before = {
        str(p.relative_to(base)): p.read_bytes() for p in base.rglob("*") if p.is_file()
    }
    audit_desktop_control(base=base, controller_file=controller, now=NOW)
    after = {
        str(p.relative_to(base)): p.read_bytes() for p in base.rglob("*") if p.is_file()
    }
    assert before == after


def test_naive_timestamp_is_rejected(tmp_path):
    base, controller = fixture_install(tmp_path)
    with pytest.raises(ValueError, match="AUDIT_TIMESTAMP_MUST_BE_TIMEZONE_AWARE"):
        audit_desktop_control(
            base=base, controller_file=controller, now=NOW.replace(tzinfo=None)
        )


def test_diagnostic_cli_never_signals_success_to_a_caller(tmp_path, capsys):
    base, controller = fixture_install(tmp_path)
    code = main(["--base", str(base), "--controller", str(controller)])
    result = json.loads(capsys.readouterr().out)
    assert code == 2
    assert result["status"] == "OWNER_CONTROL_UNVERIFIED"
    assert result["transition_performed"] is False
