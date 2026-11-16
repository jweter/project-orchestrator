"""Regression coverage for the read-only MCP/SQLite observability upgrade."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from project_orchestrator import iron_god_observability as obs


INSTANCE = "generation-abc"
SHA = "a" * 40


def _put(root: Path, relative: str, data: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _state(root: Path, when: datetime, *, product_status: str = "BLOCKED",
           canon_status: str = "IDLE_NO_READY_SLICE", worker_count: int = 0) -> None:
    at = when.isoformat()
    _put(root, "supervisor-generation-heartbeat.json",
         {"heartbeat_at_utc": at, "status": "RUNNING_CURRENT",
          "process_instance_id": INSTANCE, "actor_revision": SHA})
    _put(root, "supervisor-execution-authority.json",
         {"observed_at_utc": at, "permitted": True, "supervisor_instance_id": INSTANCE,
          "candidate_products": []})
    _put(root, "supervisor.json",
         {"updated_at_utc": at, "status": "FLOW_DEGRADED",
          "builder_status": "NO_READY_EXECUTION_SLICE",
          "authorized_products": [], "activation_reason": "IRON_GOD_COMMISSIONING_LEGACY_READINESS_SKIPPED"})
    _put(root, "iron-god-canon/lane-status.json",
         {"observed_at_utc": at, "status": canon_status})
    _put(root, "iron-god-products/lane-status.json",
         {"observed_at_utc": at, "status": product_status,
          "repository": "jweter/VoiceStudio", "worker_status": "FAIL",
          "worker_summary": "canonical preflight failed", "packet_id": "PRODUCT-TEST1"})
    _put(root, "parallel-worker-pool.json",
         {"recorded_at_utc": at, "pool": {"running_total": worker_count},
          "runtime_leases": {"leases": []}})
    _put(root, "worker-memory-admission.json",
         {"observed_at_utc": at, "status": "LOW_MEMORY_SINGLE_LANE",
          "available_mib": 4400, "new_worker_budget": 1, "execution_authorized": False})
    _put(root, "continuation-current-burst.json",
         {"updated_at_utc": at, "process_instance_id": INSTANCE,
          "last_sequence": 42, "last_builder_status": "NO_READY_EXECUTION_SLICE"})
    _put(root, "verified-delivery-throughput.json",
         {"status": "NO_AUTHORIZED_WORK", "verified_deliveries": 0,
          "product_reality_verified": 0})


def test_commissioning_legacy_empty_products_does_not_hide_actual_preflight_block(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    result = obs.snapshot(tmp_path, now=now)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "PRODUCT_CANONICAL_PREFLIGHT_FAILED"
    assert result["lanes"]["product"]["repository"] == "jweter/VoiceStudio"
    assert result["supervisor"]["execution_permitted"] is True
    assert result["supervisor"]["legacy_authorized_products"] == []
    assert result["resources"]["memory_status"] == "LOW_MEMORY_SINGLE_LANE"
    assert result["workers"]["running"] == 0
    assert result["authority"] == "READ_ONLY_NO_OWNER_GRANTS_NO_RECOVERY_EXECUTION"
    assert result["recovery_advice"]["automatic_execution"] is False
    assert result["recovery_advice"]["action"] == "REPAIR_EXISTING_PRODUCT_PREFLIGHT_OR_ROTATE_SAFE_LANE"
    assert len(result["repositories"]) == 7


def test_freshness_and_generation_are_required_to_claim_live_state(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now - timedelta(minutes=30))
    stale = obs.snapshot(tmp_path, now=now)
    assert stale["status"] == "UNVERIFIED"
    assert stale["reason"] == "RUNTIME_EVIDENCE_STALE_OR_GENERATION_MISMATCH"
    _state(tmp_path, now)
    _put(tmp_path, "supervisor-execution-authority.json",
         {"observed_at_utc": now.isoformat(), "permitted": True,
          "supervisor_instance_id": "different-generation"})
    mismatch = obs.snapshot(tmp_path, now=now)
    assert mismatch["status"] == "UNVERIFIED"


def test_missing_product_lane_not_misclassified_as_success(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    (tmp_path / "iron-god-products/lane-status.json").unlink()
    status = obs.snapshot(tmp_path, now=now)
    assert status["status"] == "IDLE"
    assert status["reason"] == "CANON_NO_READY_SLICE"
    assert status["evidence"]["product"]["status"] == "UNAVAILABLE"
    assert obs.recent_events(tmp_path)["status"] == "UNAVAILABLE"


def test_worker_running_is_not_accepted_product_progress(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now, worker_count=1)
    result = obs.snapshot(tmp_path, now=now)
    assert result["status"] == "ACTIVE_UNVERIFIED_PROGRESS"
    assert result["delivery"]["product_reality_verified"] == 0


def test_event_ledger_append_only_idempotent_and_independent(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    assert obs.record_supervisor_pass(tmp_path, now=now) is True
    assert obs.record_supervisor_pass(tmp_path, now=now) is False
    history = obs.recent_events(tmp_path)
    assert history["status"] == "OK"
    assert len(history["events"]) == 1
    assert history["events"][0]["continuation_sequence"] == 42
    assert history["events"][0]["reason"] == "PRODUCT_CANONICAL_PREFLIGHT_FAILED"

    _put(tmp_path, "continuation-current-burst.json",
         {"updated_at_utc": now.isoformat(), "process_instance_id": INSTANCE,
          "last_sequence": 43})
    assert obs.record_supervisor_pass(tmp_path, now=now) is True
    assert len(obs.recent_events(tmp_path)["events"]) == 2

    db_path = tmp_path / "observability" / "development-events.sqlite"
    with sqlite3.connect(db_path) as db:
        with pytest.raises(sqlite3.DatabaseError, match="IMMUTABLE_EVENT"):
            db.execute("DELETE FROM development_events")
        with pytest.raises(sqlite3.DatabaseError, match="IMMUTABLE_EVENT"):
            db.execute("UPDATE development_events SET status='FORGED'")

    assert not (tmp_path / "mailbox").exists()
    assert not (tmp_path / "IronGodControl").exists()


def test_unsupported_or_stale_event_cannot_mutate_database(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now - timedelta(hours=1))
    assert not obs.record_supervisor_pass(tmp_path, now=now)
    assert not (tmp_path / "observability").exists()
    with pytest.raises(ValueError, match="INVALID_EVENT_LIMIT"):
        obs.recent_events(tmp_path, limit=31)


def test_fresh_frontier_explains_approved_but_ineligible_stall(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now, product_status="IDLE_NO_READY_SLICE")
    _put(tmp_path, "iron-god-products/eligibility.json", {
        "observed_at_utc": now.isoformat(),
        "items": [
            {"repository": "jweter/VoiceStudio", "eligible": False,
             "reason": "ATTEMPTS_EXHAUSTED"},
            {"repository": "jweter/knowledge-engine-ai", "eligible": False,
             "reason": "NEEDS_DIFFERENT_CAPABILITY"},
        ],
    })
    _put(tmp_path, "iron-god-products/roadmap-refill.json",
         {"items": [{"status": "approved"}, {"status": "approved"}]})
    report = obs.snapshot(tmp_path, now=now)
    assert report["reason"] == "APPROVED_ROADMAP_WITH_NO_ELIGIBLE_SLICES"
    assert report["frontier"]["eligibility"]["reasons"] == {
        "ATTEMPTS_EXHAUSTED": 1, "NEEDS_DIFFERENT_CAPABILITY": 1,
    }
    assert report["frontier"]["refill"]["approved_roadmap_items"] == 2
    assert report["recovery_advice"]["action"] == "REFILL_AND_REEVALUATE_WITH_EXISTING_OWNER_GRANTS"
    assert report["frontier"]["eligibility_fresh"] is True
    assert report["authority"].startswith("READ_ONLY")


def test_live_owner_veto_takes_priority_over_product_preflight(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    _put(tmp_path, "supervisor-execution-authority.json", {
        "observed_at_utc": now.isoformat(), "permitted": False,
        "supervisor_instance_id": INSTANCE, "candidate_products": [],
    })
    report = obs.snapshot(tmp_path, now=now)
    assert report["reason"] == "OWNER_EXECUTION_NOT_PERMITTED"
    assert report["status"] == "BLOCKED"


def test_commissioning_product_dispatch_not_hidden_by_zero_legacy_workers(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now, product_status="DISPATCHED", worker_count=0)
    report = obs.snapshot(tmp_path, now=now)
    assert report["status"] == "ACTIVE_UNVERIFIED_PROGRESS"
    assert report["reason"] == "PRODUCT_LANE_DISPATCHED"
    assert report["recovery_advice"]["action"] == "OBSERVE_CURRENT_PACKET_NO_DUPLICATE_DISPATCH"
    assert report["workers"]["running"] == 0
    assert report["lanes"]["product"]["packet_id"] == "PRODUCT-TEST1"
    assert report["delivery"]["product_reality_verified"] == 0


def test_advisory_v2_status_is_bounded_idempotent_and_never_executes(tmp_path):
    from project_orchestrator import iron_god_advisory_mailbox as advisory
    from project_orchestrator import mcp_coordination as coord

    _state(tmp_path, datetime.now(UTC), product_status="DISPATCHED")
    db = tmp_path / "coordination.sqlite"
    message = coord.post_message(
        db, sender_label="chatgpt", recipient="iron-god", kind="question",
        body="engine_status_v2", reference="protocol:engine-status-v2",
        idempotency_key="unit-status-v2",
    )
    bad = coord.post_message(
        db, sender_label="chatgpt", recipient="iron-god", kind="question",
        body="engine_status_v2", reference="protocol:wrong-version",
        idempotency_key="unit-status-bad",
    )
    handled = advisory._answer_status_queries(db, tmp_path, after_seq=0)
    assert len(handled) == 1
    assert handled[0]["request_id"] == message["message_id"]
    reply = coord.lookup_posted_message(
        db, sender_label="iron-god",
        idempotency_key="engine-status-v2-response:" + message["message_id"],
    )
    body = json.loads(reply["body"])
    assert body["schema_version"] == 2
    assert body["engine_status"] == "ACTIVE_UNVERIFIED_PROGRESS"
    assert body["reason"] == "PRODUCT_LANE_DISPATCHED"
    assert body["product"]["repository"] == "jweter/VoiceStudio"
    assert body["authority"] == "ADVISORY_ONLY_NO_OWNER_GRANTS"
    assert reply["reference"] == "protocol:engine-status-v2"
    assert advisory._answer_status_queries(db, tmp_path, after_seq=0) == []
    pending = coord.list_pending_messages(db, recipient_label="iron-god")
    assert [m["message_id"] for m in pending["messages"]] == [bad["message_id"]]
    assert not (tmp_path / "IronGodControl").exists()
