"""S2: untrusted worker results cannot advance independent verification."""
from __future__ import annotations

from copy import deepcopy

import pytest

from project_orchestrator.worker_transport import (
    PACKET_STATES,
    WorkerTransportError,
    _digest,
    _event_id,
    dispatch_packet,
    empty_ledger,
    record_worker_result,
    requeueable_packets,
    request_supervisor_guidance,
    respond_to_supervisor_question,
    retry_packet,
    transition_packet,
    validate_ledger,
)

BASE = "a" * 40
HEAD = "b" * 40


def packet():
    return {
        "packet_id": "s2-packet", "project": "everward",
        "repository": "jweter/Project-Everward", "source_ref": "issue:240",
        "base_sha": BASE, "branch": "agent/s2-packet",
        "task": "Build a bounded source and regression-test slice",
        "paths": ["tools/worker.py"],
        "verification": ["python tools/quality_preflight.py --full"],
    }


def outcome(status="PASS", head=HEAD):
    return {
        "project": "everward", "repository": "jweter/Project-Everward",
        "branch": "agent/s2-packet", "head_sha": head,
        "probe_id": "s2-runtime", "pattern_id": "bounded-worker",
        "evidence_ref": "local:s2-worker-evidence",
        "observed_at": "2026-10-25T13:00:00Z",
        "status": status, "summary": "Worker-reported outcome only, not independent verification.",
    }


def dispatched():
    return dispatch_packet(empty_ledger(), packet(), transport="local_idle_worker")


def _reseal(value):
    value["seal_sha256"] = _digest({key: row for key, row in value.items() if key != "seal_sha256"})
    return value


def test_worker_pass_hands_back_and_never_independently_verifies():
    ledger = record_worker_result(dispatched(), "s2-packet", outcome())
    assert ledger["packets"]["s2-packet"]["state"] == "HANDED_BACK"
    assert ledger["packets"]["s2-packet"]["exact_head_sha"] == HEAD
    assert ledger["packets"]["s2-packet"]["worker_status"] == "PASS"
    assert ledger["packets"]["s2-packet"]["independent_verification"] == "NOT_VERIFIED"
    assert validate_ledger(ledger) == ledger
    with pytest.raises(WorkerTransportError, match="ILLEGAL_STATE_TRANSITION"):
        transition_packet(ledger, "s2-packet", state="PREFLIGHT_GREEN", evidence_ref="worker:claimed-green")
    with pytest.raises(WorkerTransportError, match="ILLEGAL_STATE_TRANSITION"):
        transition_packet(ledger, "s2-packet", state="MERGED", exact_head_sha=HEAD, evidence_ref="pr:1")


def test_preflight_unverified_requires_worker_handback_and_separate_result():
    initial = dispatched()
    with pytest.raises(WorkerTransportError, match="ILLEGAL_STATE_TRANSITION"):
        transition_packet(initial, "s2-packet", state="PREFLIGHT_UNVERIFIED", evidence_ref="local:preflight-start")
    handback = record_worker_result(initial, "s2-packet", outcome())
    verifying = transition_packet(
        handback, "s2-packet", state="PREFLIGHT_UNVERIFIED",
        evidence_ref="local:independent-native-check-start", exact_head_sha=HEAD,
    )
    assert verifying["packets"]["s2-packet"]["state"] == "PREFLIGHT_UNVERIFIED"
    assert verifying["packets"]["s2-packet"]["independent_verification"] == "NOT_VERIFIED"
    assert validate_ledger(verifying) == verifying
    with pytest.raises(WorkerTransportError, match="active dispatched"):
        record_worker_result(verifying, "s2-packet", outcome("FAIL"))


def test_unchanged_base_sha_is_not_worker_success():
    with pytest.raises(WorkerTransportError, match="unchanged base"):
        record_worker_result(dispatched(), "s2-packet", outcome(head=BASE))


@pytest.mark.parametrize("status,state", [
    ("FAIL", "BLOCKED"),
    ("REVIEW_REQUIRED", "BLOCKED"),
    ("PRODUCT_REALITY_REQUIRED", "PARKED"),
    ("ENVIRONMENT_FAILURE", "PARKED"),
])
def test_worker_outcomes_never_promote_to_verified(status, state):
    ledger = record_worker_result(dispatched(), "s2-packet", outcome(status=status))
    assert ledger["packets"]["s2-packet"]["state"] == state
    assert ledger["packets"]["s2-packet"]["independent_verification"] == "NOT_VERIFIED"
    with pytest.raises(WorkerTransportError, match="active dispatched"):
        record_worker_result(ledger, "s2-packet", outcome("PASS"))


@pytest.mark.parametrize("target", sorted(PACKET_STATES - {"IMPLEMENTING", "CANCELLED"}))
def test_no_illegal_direct_edges_from_dispatched(target):
    with pytest.raises(WorkerTransportError):
        transition_packet(
            dispatched(), "s2-packet", state=target,
            evidence_ref="supervisor:unproven", exact_head_sha=HEAD,
        )


@pytest.mark.parametrize("state", [
    "PREFLIGHT_GREEN", "PR_OPEN", "CI_PENDING", "GREEN",
    "PROMOTION_READY", "MERGED", "LEARNING_EXTRACTED",
])
def test_generic_transitions_cannot_grant_any_promotion_state(state):
    handback = record_worker_result(dispatched(), "s2-packet", outcome())
    with pytest.raises(WorkerTransportError, match="ILLEGAL_STATE_TRANSITION"):
        transition_packet(handback, "s2-packet", state=state, evidence_ref="pr:1")


def test_cancellation_requires_evidence_and_is_terminal():
    with pytest.raises(WorkerTransportError, match="requires evidence"):
        transition_packet(dispatched(), "s2-packet", state="CANCELLED")
    cancelled = transition_packet(
        dispatched(), "s2-packet", state="CANCELLED", evidence_ref="owner:slice-withdrawn",
    )
    assert cancelled["packets"]["s2-packet"]["state"] == "CANCELLED"
    with pytest.raises(WorkerTransportError, match="terminal"):
        transition_packet(cancelled, "s2-packet", state="IMPLEMENTING")
    with pytest.raises(WorkerTransportError):
        record_worker_result(cancelled, "s2-packet", outcome())


def test_duplicate_transition_is_exactly_idempotent_but_modified_replay_rejected():
    running = transition_packet(dispatched(), "s2-packet", state="IMPLEMENTING")
    assert transition_packet(running, "s2-packet", state="IMPLEMENTING") == running
    with pytest.raises(WorkerTransportError, match="state replay"):
        transition_packet(running, "s2-packet", state="IMPLEMENTING", evidence_ref="changed")


def test_questions_cannot_be_synthesized_through_transition():
    with pytest.raises(WorkerTransportError):
        transition_packet(dispatched(), "s2-packet", state="WAITING_FOR_SUPERVISOR")
    awaiting = request_supervisor_guidance(
        dispatched(), "s2-packet", question_id="q1", worker_id="worker-1",
        summary="Need to clarify project scope", evidence_ref="local:issue",
        expected_base_sha=BASE,
    )
    assert awaiting["packets"]["s2-packet"]["state"] == "WAITING_FOR_SUPERVISOR"
    answered = respond_to_supervisor_question(
        awaiting, "s2-packet", question_id="q1", decision="CONTINUE_WITHIN_SCOPE",
        guidance="Proceed only within original scoped files", expected_base_sha=BASE,
    )
    assert answered["packets"]["s2-packet"]["state"] == "DISPATCHED"
    assert validate_ledger(answered) == answered


def test_explicit_retry_preserves_failures_and_has_a_strict_limit():
    ledger = record_worker_result(dispatched(), "s2-packet", outcome("FAIL"))
    assert [row["packet_id"] for row in requeueable_packets(ledger)] == ["s2-packet"]
    with pytest.raises(WorkerTransportError, match="redispatch"):
        dispatch_packet(ledger, packet(), transport="local_idle_worker")
    for count in (2, 3):
        ledger = retry_packet(
            ledger, "s2-packet", expected_base_sha=BASE,
            remediation_evidence_ref="local:root-cause-reproduced",
        )
        assert ledger["packets"]["s2-packet"]["attempt_count"] == count
        assert ledger["packets"]["s2-packet"]["previous_results"][-1]["status"] == "FAIL"
        ledger = record_worker_result(ledger, "s2-packet", outcome("FAIL"))
    assert requeueable_packets(ledger) == []
    with pytest.raises(WorkerTransportError, match="budget exhausted"):
        retry_packet(
            ledger, "s2-packet", expected_base_sha=BASE,
            remediation_evidence_ref="local:another-try",
        )
    assert ledger["revision"] == 6
    assert validate_ledger(ledger) == ledger


@pytest.mark.parametrize("status", ["REVIEW_REQUIRED", "PRODUCT_REALITY_REQUIRED"])
def test_retry_never_bypasses_review_or_product_reality(status):
    ledger = record_worker_result(dispatched(), "s2-packet", outcome(status))
    assert requeueable_packets(ledger) == []
    with pytest.raises(WorkerTransportError, match="human/review"):
        retry_packet(
            ledger, "s2-packet", expected_base_sha=BASE,
            remediation_evidence_ref="issue:approved-by-worker",
        )


def test_retry_rejects_old_head_and_missing_remediation_evidence():
    failed = record_worker_result(dispatched(), "s2-packet", outcome("FAIL"))
    with pytest.raises(WorkerTransportError, match="base mismatch"):
        retry_packet(
            failed, "s2-packet", expected_base_sha=HEAD,
            remediation_evidence_ref="local:root-cause",
        )
    with pytest.raises(WorkerTransportError, match="sanitized"):
        retry_packet(
            failed, "s2-packet", expected_base_sha=BASE,
            remediation_evidence_ref="",
        )


def test_resealed_illegal_dispatch_and_self_verified_worker_event_are_rejected():
    corrupted = deepcopy(dispatched())
    corrupted["events"][0]["state_after"] = "MERGED"
    corrupted["events"][0]["event_id"] = _event_id(corrupted["events"][0])
    corrupted["packets"]["s2-packet"]["state"] = "MERGED"
    with pytest.raises(WorkerTransportError, match="invalid initial dispatch"):
        validate_ledger(_reseal(corrupted))

    self_verified = deepcopy(record_worker_result(dispatched(), "s2-packet", outcome()))
    self_verified["events"][-1]["state_after"] = "PREFLIGHT_GREEN"
    self_verified["events"][-1]["event_id"] = _event_id(self_verified["events"][-1])
    self_verified["packets"]["s2-packet"]["state"] = "PREFLIGHT_GREEN"
    with pytest.raises(WorkerTransportError, match="status illegally promoted"):
        validate_ledger(_reseal(self_verified))


def test_non_worker_can_only_mark_a_blocked_unverified_preflight_with_evidence():
    handback = record_worker_result(dispatched(), "s2-packet", outcome())
    verifying = transition_packet(
        handback, "s2-packet", state="PREFLIGHT_UNVERIFIED",
        evidence_ref="local:checker-running",
    )
    with pytest.raises(WorkerTransportError, match="requires evidence"):
        transition_packet(verifying, "s2-packet", state="BLOCKED")
    blocked = transition_packet(
        verifying, "s2-packet", state="BLOCKED",
        evidence_ref="local:native-suite-failed",
    )
    assert blocked["packets"]["s2-packet"]["state"] == "BLOCKED"
    assert blocked["packets"]["s2-packet"]["independent_verification"] == "NOT_VERIFIED"
    with pytest.raises(WorkerTransportError, match="active dispatched"):
        record_worker_result(blocked, "s2-packet", outcome("PASS"))


def _reachable(origin):
    initial = dispatched()
    if origin == "DISPATCHED":
        return initial
    if origin == "IMPLEMENTING":
        return transition_packet(initial, "s2-packet", state="IMPLEMENTING")
    if origin == "WAITING_FOR_SUPERVISOR":
        return request_supervisor_guidance(
            initial, "s2-packet", question_id="wait", worker_id="worker-1",
            summary="Supervisor guidance needed", evidence_ref="local:obstacle",
            expected_base_sha=BASE,
        )
    if origin == "HANDED_BACK":
        return record_worker_result(initial, "s2-packet", outcome())
    if origin == "PREFLIGHT_UNVERIFIED":
        return transition_packet(
            _reachable("HANDED_BACK"), "s2-packet", state=origin,
            evidence_ref="local:checks-started",
        )
    if origin == "BLOCKED":
        return record_worker_result(initial, "s2-packet", outcome("FAIL"))
    if origin == "PARKED":
        return record_worker_result(initial, "s2-packet", outcome("ENVIRONMENT_FAILURE"))
    if origin == "RECONCILE_REQUIRED":
        return transition_packet(
            _reachable("BLOCKED"), "s2-packet", state=origin,
            evidence_ref="supervisor:needs-replan",
        )
    if origin == "CANCELLED":
        return transition_packet(
            initial, "s2-packet", state=origin,
            evidence_ref="owner:explicitly-withdrawn",
        )
    raise ValueError(origin)


@pytest.mark.parametrize("origin", [
    "DISPATCHED", "IMPLEMENTING", "WAITING_FOR_SUPERVISOR",
    "HANDED_BACK", "PREFLIGHT_UNVERIFIED", "BLOCKED",
    "PARKED", "RECONCILE_REQUIRED", "CANCELLED",
])
def test_every_unsupported_edge_denied_for_reachable_states(origin):
    from project_orchestrator.worker_transport import ALLOWED_STATE_EDGES
    initial = _reachable(origin)
    safe_targets = ALLOWED_STATE_EDGES.get(origin, frozenset())
    for target in sorted(PACKET_STATES - safe_targets):
        with pytest.raises(WorkerTransportError):
            transition_packet(
                initial, "s2-packet", state=target,
                evidence_ref="test:non-identical-attempt",
            )
    assert validate_ledger(initial) == initial


def test_separate_worker_result_after_supervisor_park_cannot_resume_automatically():
    pending = _reachable("WAITING_FOR_SUPERVISOR")
    stopped = respond_to_supervisor_question(
        pending, "s2-packet", question_id="wait", decision="PARK_SLICE",
        guidance="Pause this slice; external review required.", expected_base_sha=BASE,
    )
    assert requeueable_packets(stopped) == []
    with pytest.raises(WorkerTransportError):
        retry_packet(
            stopped, "s2-packet", expected_base_sha=BASE,
            remediation_evidence_ref="worker:ignore-review",
        )
    with pytest.raises(WorkerTransportError):
        record_worker_result(stopped, "s2-packet", outcome("PASS"))
