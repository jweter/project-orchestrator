"""Dashboard reports cross-lane evidence without granting or inventing control authority."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from project_orchestrator import human_ownership

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard_truth", ROOT / "scripts" / "foundry_dashboard_server.py")
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)
NOW = datetime(2026, 11, 8, 18, 30, tzinfo=timezone.utc)


def store(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_distinct_lanes_are_attributed_and_stale_evidence_is_not_live(tmp_path):
    current = NOW.isoformat()
    old = (NOW - timedelta(minutes=20)).isoformat()
    store(tmp_path / "iron-god-products/lane-status.json", {
        "status": "DISPATCHED", "observed_at_utc": current,
        "repository": "jweter/knowledge-engine-ai",
        "backlog_item": "ai-online-report", "worker_pid": 2820,
    })
    store(tmp_path / "iron-god-canon/lane-status.json", {
        "status": "IDLE_NO_READY_SLICE", "observed_at_utc": old,
    })
    store(tmp_path / "parallel-worker-pool.json", {
        "recorded_at_utc": current, "pool": {"running_total": 0},
    })
    store(tmp_path / "supervisor-execution-authority.json", {
        "observed_at_utc": current, "permitted": True,
        "candidate_products": [], "supervisor_instance_id": "supervisor-1",
    })
    lanes = dashboard._development_lanes(tmp_path, now=NOW)
    assert lanes["product"]["status"] == "DISPATCHED"
    assert lanes["product"]["repository"] == "jweter/knowledge-engine-ai"
    assert lanes["product"]["worker_pid"] == 2820
    assert lanes["canon"]["status"] == "STALE"
    assert lanes["canon"]["reported_status"] == "IDLE_NO_READY_SLICE"
    assert lanes["legacy_pool"]["status"] == "REPORTED"
    assert lanes["legacy_pool"]["running_total"] == 0
    assert lanes["dispatch"]["permitted"] is True
    assert lanes["dispatch"]["candidate_products"] == []


def test_missing_and_unstamped_lane_records_fail_closed(tmp_path):
    lanes = dashboard._development_lanes(tmp_path, now=NOW)
    assert all(lane["status"] == "UNKNOWN" and not lane["fresh"] for lane in lanes.values())
    store(tmp_path / "iron-god-products/lane-status.json", {
        "status": "DISPATCHED", "worker_pid": 1234,
    })
    product = dashboard._development_lanes(tmp_path, now=NOW)["product"]
    assert product["status"] == "STALE" and product["reported_status"] == "DISPATCHED"
    store(tmp_path / "iron-god-products/lane-status.json", {
        "status": "DISPATCHED", "observed_at_utc": (NOW + timedelta(minutes=5)).isoformat(),
    })
    future = dashboard._development_lanes(tmp_path, now=NOW)["product"]
    assert future["status"] == "STALE" and not future["fresh"]


def test_owner_mode_reads_authoritative_controller_not_retired_marker(tmp_path, monkeypatch):
    store(tmp_path / "human-mode.json", {"enabled": False, "since": "retired"})
    monkeypatch.setattr(human_ownership, "owner_control_status", lambda _: human_ownership.OWNER_CONTROL_ACTIVE)
    mode = dashboard._owner_mode(tmp_path)
    assert mode == {"enabled": True, "status": human_ownership.OWNER_CONTROL_ACTIVE, "since": None}
    monkeypatch.setattr(human_ownership, "owner_control_status", lambda _: human_ownership.OWNER_CONTROL_UNVERIFIED)
    unknown = dashboard._owner_mode(tmp_path)
    assert unknown["enabled"] is None and unknown["status"] == human_ownership.OWNER_CONTROL_UNVERIFIED


def test_dashboard_does_not_present_missing_control_as_full_power():
    js = (ROOT / "dashboard/app.js").read_text(encoding="utf-8")
    html = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
    assert 'mode.enabled===true||mode.enabled===false' in js
    assert "Controller status unknown" in js
    assert 'id="lanes"' in html
    assert "Product lane dispatch reported" in js
    assert "UNVERIFIED LIVE PID" in js
    assert "No legacy workers reported" in js
