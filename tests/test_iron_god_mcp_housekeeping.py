"""Housekeeping is visible through MCP but never grants deletion authority."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from project_orchestrator import iron_god_mcp_housekeeping as view


def _receipt(state, payload):
    path = state / "iron-god-products" / "reports" / "storage-housekeeping.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid(now):
    gib = 2 ** 30
    return {
        "schema_version": 1,
        "mode": "READ_ONLY_ACCOUNTING_NO_DELETION",
        "observed_at_utc": now.isoformat(),
        "disk": {"status": "OBSERVED", "free_bytes": 35 * gib,
                 "total_bytes": 500 * gib, "band": "ORANGE",
                 "volume": "C:\\", "ignored_secret": "not-for-clients"},
        "counts": {
            "product_worktrees": {"status": "OBSERVED", "count": 26},
            "recovery_archive_files": {"status": "OBSERVED", "count": 255},
        },
        "last_cleanup": {"status": "REPORTED", "removed": 3,
                         "left_for_next_run": 15,
                         "results": {"P-SECRET": "DELETE_ATTEMPTED"}},
    }


def test_housekeeping_mcp_projection_returns_only_safe_verified_fields(tmp_path):
    now = datetime.now(UTC)
    _receipt(tmp_path, _valid(now))
    payload = view.housekeeping_snapshot(tmp_path, now=now)
    assert payload["status"] == "OK"
    assert payload["disk"] == {"band": "ORANGE", "free_gib": 35.0,
                               "total_gib": 500.0}
    assert payload["counts"]["product_worktrees"] == {"status": "OBSERVED", "count": 26}
    assert payload["maintenance"] == {
        "status": "REPORTED", "reported_removed": 3, "remaining_eligible": 15,
    }
    assert payload["can_execute"] is False
    assert payload["free_space_delta_attributable_to_cleanup"] is False
    encoded = json.dumps(payload)
    assert "P-SECRET" not in encoded and "ignored_secret" not in encoded
    assert "C:\\" not in encoded


def test_missing_corrupt_and_symlinked_receipts_fail_closed(tmp_path):
    assert view.housekeeping_snapshot(tmp_path)["status"] == "UNAVAILABLE"
    path = _receipt(tmp_path, {"oops": "missing schema"})
    assert view.housekeeping_snapshot(tmp_path)["status"] == "UNAVAILABLE"
    path.write_bytes(b"broken JSON")
    assert view.housekeeping_snapshot(tmp_path)["status"] == "UNAVAILABLE"
    path.write_bytes(b"0" * (view.MAX_RECEIPT_BYTES + 1))
    assert view.housekeeping_snapshot(tmp_path)["status"] == "UNAVAILABLE"
    path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_valid(datetime.now(UTC))), encoding="utf-8")
    try:
        path.symlink_to(outside)
    except (OSError, NotImplementedError):
        return
    assert view.housekeeping_snapshot(tmp_path)["status"] == "UNAVAILABLE"


def test_stale_receipt_never_claims_live_health(tmp_path):
    now = datetime.now(UTC)
    _receipt(tmp_path, _valid(now - timedelta(hours=3)))
    result = view.housekeeping_snapshot(tmp_path, now=now)
    assert result["status"] == "STALE"
    assert result["freshness"] == "STALE"
    assert result["can_execute"] is False


def test_invalid_disk_metrics_cannot_report_healthy_disk(tmp_path):
    now = datetime.now(UTC)
    source = _valid(now)
    source["disk"]["free_bytes"] = source["disk"]["total_bytes"] + 1
    _receipt(tmp_path, source)
    result = view.housekeeping_snapshot(tmp_path, now=now)
    assert result["disk"]["band"] == "UNKNOWN"
    assert result["disk"]["free_gib"] is None


def test_maintenance_failure_does_not_look_like_a_success(tmp_path):
    now = datetime.now(UTC)
    source = _valid(now)
    source["last_cleanup"] = {"status": "FAILED", "reason": "private filesystem exception"}
    _receipt(tmp_path, source)
    result = view.housekeeping_snapshot(tmp_path, now=now)
    assert result["maintenance"]["status"] == "FAILED"
    assert "private filesystem exception" not in json.dumps(result)
    assert result["maintenance"]["reported_removed"] is None
