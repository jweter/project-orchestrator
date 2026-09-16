import pytest

from project_orchestrator.worker_transport import (
    WorkerTransportError,
    dispatch_packet,
    empty_ledger,
    record_worker_result,
    requeueable_packets,
    transition_packet,
    validate_ledger,
)

SHA_A = "a" * 40
SHA_B = "b" * 40


def packet():
    return {
        "packet_id": "pkt-1",
        "project": "everward",
        "repository": "jweter/Project-Everward",
        "source_ref": "jweter/Project-Everward#240",
        "base_sha": SHA_A,
        "branch": "agent/pkt-1",
        "task": "run bounded unattended verification slice",
        "paths": ["tools/worker.py"],
        "verification": ["python tools/quality_preflight.py --full"],
        "capabilities_to_reuse": [],
    }


def worker(status="PASS", **overrides):
    item = {
        "project": "everward",
        "repository": "jweter/Project-Everward",
        "branch": "agent/pkt-1",
        "head_sha": SHA_B,
        "probe_id": "everward-unattended",
        "pattern_id": "exact-head-verification",
        "evidence_ref": "issue:243#latest",
        "observed_at": "2026-09-16T23:30:00Z",
        "status": status,
        "summary": "sanitized verification result",
    }
    item.update(overrides)
    return item


def test_dispatch_is_idempotent_for_same_packet_and_transport():
    first = dispatch_packet(empty_ledger(), packet(), transport="local_idle_worker")
    second = dispatch_packet(first, packet(), transport="local_idle_worker")
    assert second == first
    assert len(second["events"]) == 1


def test_worker_pass_binds_exact_head_and_advances_to_preflight_green():
    ledger = dispatch_packet(empty_ledger(), packet(), transport="local_idle_worker")
    ledger = record_worker_result(ledger, "pkt-1", worker())
    assert ledger["packets"]["pkt-1"]["state"] == "PREFLIGHT_GREEN"
    assert ledger["packets"]["pkt-1"]["exact_head_sha"] == SHA_B


def test_environment_failure_parks_only_packet_lane():
    ledger = dispatch_packet(empty_ledger(), packet(), transport="local_idle_worker")
    ledger = record_worker_result(ledger, "pkt-1", worker("ENVIRONMENT_FAILURE"))
    assert ledger["packets"]["pkt-1"]["state"] == "PARKED"
    assert [item["packet_id"] for item in requeueable_packets(ledger)] == ["pkt-1"]


def test_mismatched_repository_fails_closed():
    ledger = dispatch_packet(empty_ledger(), packet(), transport="local_idle_worker")
    with pytest.raises(WorkerTransportError):
        record_worker_result(ledger, "pkt-1", worker(repository="jweter/knowledge-engine-ai", project="knowledge-engine"))


def test_terminal_packet_cannot_be_redispatched_or_reopened():
    ledger = dispatch_packet(empty_ledger(), packet(), transport="github_event")
    ledger = transition_packet(ledger, "pkt-1", state="MERGED", exact_head_sha=SHA_B, evidence_ref="pr:1")
    with pytest.raises(WorkerTransportError):
        dispatch_packet(ledger, packet(), transport="github_event")
    with pytest.raises(WorkerTransportError):
        transition_packet(ledger, "pkt-1", state="DISPATCHED")


def test_changed_base_requires_reconciliation_before_redispatch():
    ledger = dispatch_packet(empty_ledger(), packet(), transport="n8n")
    changed = packet()
    changed["base_sha"] = SHA_B
    with pytest.raises(WorkerTransportError):
        dispatch_packet(ledger, changed, transport="n8n")


def test_worker_packet_never_grants_merge_authority():
    bad = packet()
    bad["merge_authority"] = True
    with pytest.raises(WorkerTransportError):
        dispatch_packet(empty_ledger(), bad, transport="github_event")


def test_event_replay_deduplicates_identical_state_transition():
    ledger = dispatch_packet(empty_ledger(), packet(), transport="github_event")
    first = transition_packet(ledger, "pkt-1", state="CI_PENDING", exact_head_sha=SHA_B)
    second = transition_packet(first, "pkt-1", state="CI_PENDING", exact_head_sha=SHA_B)
    assert second == first


def test_ledger_validation_rejects_tampered_event_identity():
    ledger = dispatch_packet(empty_ledger(), packet(), transport="github_event")
    ledger["events"][0]["transport"] = "tampered"
    with pytest.raises(WorkerTransportError):
        validate_ledger(ledger)
