"""IRON CONTINUUM: daily verified-update status is advisory MCP evidence only."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from project_orchestrator import iron_god_engineering_evidence as evidence


OLD_SHA = "a" * 40
NEW_SHA = "b" * 40


def _report(root, stamp, *, installed=OLD_SHA, latest=NEW_SHA,
            state="UPDATE_PENDING_OWNER_CONTROL"):
    state_root = root / "iron-god-self-update"
    state_root.mkdir(parents=True, exist_ok=True)
    (root / "supervisor-pinned-release.json").write_text(
        json.dumps({"accepted_sha": installed, "accepted_repo": "local/pinned"}),
        encoding="utf-8",
    )
    record = {
        "schema_version": 1,
        "observed_at_utc": stamp.isoformat(),
        "repository": "jweter/project-orchestrator",
        "branch": "main",
        "status": state,
        "detail": "SECRET_PLAINTEXT_SHOULD_NOT_LEAK",
        "installed_before": OLD_SHA,
        "latest_main_observed": latest,
        "installed_after": installed,
        "advisory_only": True,
        "owner_grant_created": False,
        "workers_stopped": False,
        "task_triggered_engine_start": False,
    }
    (state_root / "daily-status.json").write_text(json.dumps(record), encoding="utf-8")
    return record


def test_missing_daily_status_does_not_create_files_or_maintenance_authority(tmp_path):
    result = evidence.daily_release_update_evidence(tmp_path)
    assert result["status"] == "UNAVAILABLE"
    assert result["pending_update_as_observed"] is False
    assert result["install_authorized_by_mcp"] is False
    assert result["restart_authorized"] is False
    assert list(tmp_path.iterdir()) == []


def test_fresh_pending_release_visible_with_pinned_sha_and_no_execution_grant(tmp_path):
    now = datetime.now(UTC)
    _report(tmp_path, now)
    result = evidence.daily_release_update_evidence(tmp_path, now=now)
    assert result["status"] == "OBSERVED_FRESH"
    assert result["reported_update_status"] == "UPDATE_PENDING_OWNER_CONTROL"
    assert result["current_pinned_sha"] == OLD_SHA
    assert result["observed_github_main_sha"] == NEW_SHA
    assert result["pending_update_as_observed"] is True
    assert result["owner_control_required"] is True
    assert result["stop_workers_authorized"] is False
    assert result["restart_authorized"] is False
    assert result["install_authorized_by_mcp"] is False
    assert result["github_ci_verified"] is False
    assert "SECRET_" not in json.dumps(result)
    bundled = evidence.engineering_bundle(tmp_path, now=now)
    assert bundled["delivery"]["daily_verified_update"] == result


def test_stale_report_never_remains_a_live_update_instruction(tmp_path):
    now = datetime.now(UTC)
    _report(tmp_path, now - timedelta(hours=40))
    result = evidence.daily_release_update_evidence(tmp_path, now=now)
    assert result["status"] == "OBSERVED_STALE"
    assert result["pending_update_as_observed"] is False
    assert result["restart_authorized"] is False


def test_prior_report_after_manual_install_has_pinned_sha_drift(tmp_path):
    now = datetime.now(UTC)
    _report(tmp_path, now, installed=OLD_SHA)
    (tmp_path / "supervisor-pinned-release.json").write_text(
        json.dumps({"accepted_sha": NEW_SHA}), encoding="utf-8",
    )
    result = evidence.daily_release_update_evidence(tmp_path, now=now)
    assert result["status"] == "OBSERVED_STALE"
    assert result["pending_update_as_observed"] is False
    assert result["installed_pin_matches_report"] is False


def test_forged_or_corrupt_record_fails_closed(tmp_path):
    now = datetime.now(UTC)
    raw = _report(tmp_path, now)
    file = tmp_path / "iron-god-self-update" / "daily-status.json"
    raw["owner_grant_created"] = True
    file.write_text(json.dumps(raw), encoding="utf-8")
    assert evidence.daily_release_update_evidence(tmp_path)["status"] == "UNVERIFIED"
    file.write_text("{bad json", encoding="utf-8")
    assert evidence.daily_release_update_evidence(tmp_path)["status"] == "UNVERIFIED"


def test_contradictory_update_pending_report_does_not_claim_available_update(tmp_path):
    now = datetime.now(UTC)
    _report(tmp_path, now, latest=OLD_SHA)
    data = evidence.daily_release_update_evidence(tmp_path, now=now)
    assert data["status"] == "OBSERVED_UNVERIFIED"
    assert data["pending_update_as_observed"] is False


def test_reported_success_does_not_claim_independent_release_installation(tmp_path):
    now = datetime.now(UTC)
    _report(tmp_path, now, installed=NEW_SHA,
            state="UPDATED_DISABLED_OWNER_RESUME_REQUIRED")
    data = evidence.daily_release_update_evidence(tmp_path, now=now)
    assert data["status"] == "OBSERVED_FRESH"
    assert data["reported_update_status"] == "UPDATED_DISABLED_OWNER_RESUME_REQUIRED"
    assert data["installed_release_verified"] is False
    assert data["install_authorized_by_mcp"] is False
    assert data["restart_authorized"] is False
