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

from project_orchestrator.worker_transport import (
    read_supervisor_response,
    request_supervisor_guidance,
    respond_to_supervisor_question,
)


def _ask(ledger, **overrides):
    values = {
        "question_id": "issue-1",
        "worker_id": "everward-worker-1",
        "summary": "Unclear Unreal build evidence; need scope-safe direction.",
        "evidence_ref": "local:unreal-log-1",
        "expected_base_sha": SHA_A,
    }
    values.update(overrides)
    return request_supervisor_guidance(ledger, "pkt-1", **values)


def _answer(ledger, **overrides):
    values = {
        "question_id": "issue-1",
        "decision": "CONTINUE_WITHIN_SCOPE",
        "guidance": "Run the existing native test; do not expand paths.",
        "expected_base_sha": SHA_A,
    }
    values.update(overrides)
    return respond_to_supervisor_question(ledger, "pkt-1", **values)


def test_worker_can_pause_and_retrieve_supervisor_answer_without_grant_mutation():
    issued = dispatch_packet(empty_ledger(), packet(), transport="local_idle_worker")
    pending = _ask(issued)
    assert pending["packets"]["pkt-1"]["state"] == "WAITING_FOR_SUPERVISOR"
    assert pending["packets"]["pkt-1"]["paths"] == ["tools/worker.py"]
    assert pending["packets"]["pkt-1"]["base_sha"] == SHA_A
    assert _ask(pending) == pending  # idempotent retry, one question event
    assert [event["type"] for event in pending["events"]] == ["DISPATCHED", "WORKER_QUESTION"]
    with pytest.raises(WorkerTransportError, match="supervisor question"):
        record_worker_result(pending, "pkt-1", worker())
    with pytest.raises(WorkerTransportError, match="supervisor question"):
        transition_packet(pending, "pkt-1", state="PREFLIGHT_GREEN")
    with pytest.raises(WorkerTransportError, match="supervisor exchange"):
        dispatch_packet(pending, packet(), transport="local_idle_worker")

    answered = _answer(pending)
    assert answered["packets"]["pkt-1"]["state"] == "DISPATCHED"
    reply = read_supervisor_response(answered, "pkt-1", question_id="issue-1", expected_base_sha=SHA_A)
    assert reply["status"] == "ANSWERED"
    assert reply["decision"] == "CONTINUE_WITHIN_SCOPE"
    assert answered["packets"]["pkt-1"]["paths"] == ["tools/worker.py"]
    green = record_worker_result(answered, "pkt-1", worker())
    assert green["packets"]["pkt-1"]["state"] == "PREFLIGHT_GREEN"


def test_supervisor_replan_prevents_automatic_resume_or_redispatch():
    pending = _ask(dispatch_packet(empty_ledger(), packet(), transport="github_event"))
    blocked = _answer(pending, decision="REPLAN_REQUIRED", guidance="Vision and roadmap disagree; request new approved slice.")
    assert blocked["packets"]["pkt-1"]["state"] == "RECONCILE_REQUIRED"
    assert requeueable_packets(blocked) == []
    with pytest.raises(WorkerTransportError):
        dispatch_packet(blocked, packet(), transport="github_event")
    with pytest.raises(WorkerTransportError):
        record_worker_result(blocked, "pkt-1", worker())
    with pytest.raises(WorkerTransportError):
        transition_packet(blocked, "pkt-1", state="MERGED")


def test_supervisor_park_stops_slice_and_keeps_other_lanes_available():
    first = dispatch_packet(empty_ledger(), packet(), transport="local_idle_worker")
    independent = packet()
    independent.update({"packet_id": "pkt-2", "repository": "jweter/VoiceStudio", "project": "voicestudio"})
    combined = dispatch_packet(first, independent, transport="local_idle_worker")
    parked = _answer(_ask(combined), decision="PARK_SLICE", guidance="Native hardware absent; preserve work.")
    assert parked["packets"]["pkt-1"]["state"] == "PARKED"
    assert parked["packets"]["pkt-2"]["state"] == "DISPATCHED"


@pytest.mark.parametrize("bad", [
    {"expected_base_sha": SHA_B},
    {"question_id": "../unstable"},
    {"summary": "password=supersecret"},
    {"summary": "x" * 1201},
    {"summary": "line\x00unsafe"},
    {"worker_id": ""},
])
def test_worker_question_rejects_bad_identity_and_unsafe_payload(bad):
    ledger = dispatch_packet(empty_ledger(), packet(), transport="local_idle_worker")
    with pytest.raises(WorkerTransportError):
        _ask(ledger, **bad)


def test_supervisor_response_requires_exact_identity_and_known_decision():
    pending = _ask(dispatch_packet(empty_ledger(), packet(), transport="n8n"))
    with pytest.raises(WorkerTransportError):
        _answer(pending, expected_base_sha=SHA_B)
    with pytest.raises(WorkerTransportError):
        _answer(pending, question_id="other")
    with pytest.raises(WorkerTransportError):
        _answer(pending, decision="OVERRIDE_ALLOWED_PATHS")
    with pytest.raises(WorkerTransportError):
        _answer(pending, guidance="api_key=secret")
    assert pending["packets"]["pkt-1"]["state"] == "WAITING_FOR_SUPERVISOR"


def test_multiple_sequential_questions_preserve_boundaries():
    ledger = dispatch_packet(empty_ledger(), packet(), transport="n8n")
    ledger = _answer(_ask(ledger))
    ledger = _answer(_ask(ledger, question_id="issue-2"), question_id="issue-2")
    assert [row["question_id"] for row in ledger["packets"]["pkt-1"]["supervisor_history"]] == ["issue-1"]
    with pytest.raises(WorkerTransportError):
        _ask(ledger, question_id="issue-1")
    assert ledger["packets"]["pkt-1"]["state"] == "DISPATCHED"


def test_supervisor_exchange_survives_ledger_serialization(tmp_path):
    ledger = _answer(_ask(dispatch_packet(empty_ledger(), packet(), transport="local_idle_worker")))
    from project_orchestrator.worker_transport import load_ledger, write_ledger
    path = tmp_path / "transport.json"
    write_ledger(path, ledger)
    loaded = load_ledger(path)
    assert loaded == ledger
    assert read_supervisor_response(loaded, "pkt-1", question_id="issue-1", expected_base_sha=SHA_A)["status"] == "ANSWERED"

from project_orchestrator.worker_transport import (
    dispatch_canonical_slice,
    validate_canonical_slice_plan,
)


def canonical_packet():
    result = packet()
    result["planning"] = {
        "vision_ref": "docs/VISION.md@observed-head",
        "roadmap_ref": "docs/ROADMAP.md@observed-head",
        "architecture_ref": "docs/ARCHITECTURE.md@observed-head",
        "current_state_ref": "local:current-git-and-status",
        "dependency_evidence_ref": "local:dependency-readiness",
        "source_head_sha": SHA_A,
        "objective": result["task"],
        "acceptance_criteria": ["Native checks pass on exact candidate head", "No paths outside authority"],
        "allowed_paths": ["tools/worker.py"],
        "forbidden_paths": ["DoWork.txt", "engineering/control-plane.json"],
        "verification_commands": ["python tools/quality_preflight.py --full"],
        "expected_artifacts": ["tools/worker.py", "preflight-evidence.json"],
        "run_budget_minutes": 45,
        "handoff_instructions": "Read current roadmap/vision and repository policy. Implement exactly this slice; ask the supervisor and pause if blocked. Return exact-head tests and evidence.",
        "escalation_policy": "ASK_SUPERVISOR_AND_PAUSE",
    }
    return result


def test_canonical_handoff_binds_vision_roadmap_state_and_bounded_slice():
    plan = canonical_packet()
    normalized = validate_canonical_slice_plan(plan)
    assert normalized["planning"]["source_head_sha"] == SHA_A
    ledger = dispatch_canonical_slice(empty_ledger(), plan, transport="local_idle_worker")
    assert ledger["packets"]["pkt-1"]["planning"]["run_budget_minutes"] == 45
    assert ledger["packets"]["pkt-1"]["planning"]["vision_ref"]
    with pytest.raises(WorkerTransportError):
        changed = canonical_packet()
        changed["planning"]["vision_ref"] = "different-vision"
        dispatch_canonical_slice(ledger, changed, transport="local_idle_worker")


@pytest.mark.parametrize("field,invalid", [
    ("vision_ref", ""),
    ("roadmap_ref", ""),
    ("current_state_ref", None),
    ("source_head_sha", SHA_B),
    ("objective", "unapproved replacement"),
    ("allowed_paths", []),
    ("forbidden_paths", "not-a-list"),
    ("verification_commands", []),
    ("expected_artifacts", []),
    ("run_budget_minutes", 0),
    ("run_budget_minutes", 150),
    ("run_budget_minutes", True),
    ("escalation_policy", "SELF_APPROVE"),
    ("handoff_instructions", "password=not-allowed"),
])
def test_canonical_handoff_refuses_unplanned_or_unbounded_slice(field, invalid):
    candidate = canonical_packet()
    candidate["planning"][field] = invalid
    with pytest.raises(WorkerTransportError):
        dispatch_canonical_slice(empty_ledger(), candidate, transport="local_idle_worker")


def test_canonical_handoff_does_not_grant_worker_merge_authority():
    candidate = canonical_packet()
    candidate["merge_authority"] = True
    with pytest.raises(WorkerTransportError):
        dispatch_canonical_slice(empty_ledger(), candidate, transport="github_event")


def test_canonical_handoff_only_updates_existing_ledger_and_does_not_run_builder():
    candidate = canonical_packet()
    result = dispatch_canonical_slice(empty_ledger(), candidate, transport="local_idle_worker")
    assert result["packets"]["pkt-1"]["state"] == "DISPATCHED"
    assert [e["type"] for e in result["events"]] == ["DISPATCHED"]
