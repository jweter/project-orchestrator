import json
import threading

import pytest

import project_orchestrator.grant_lifecycle as lifecycle
import project_orchestrator.state_io as state_io
import scripts.autonomous_slice_builder as builder
import scripts.continuous_flow_supervisor as supervisor


def test_builder_uses_shared_lifecycle_primitives_and_supervisor_does_not_reown_them():
    assert builder.mutate_grant_lifecycle is lifecycle.mutate_grant_lifecycle
    assert builder._acquire_lifecycle_lock is lifecycle.acquire_lifecycle_lock
    assert builder._release_lifecycle_lock is lifecycle.release_lifecycle_lock
    assert not hasattr(supervisor, "mutate_grant_lifecycle")
    assert not hasattr(supervisor, "_acquire_lifecycle_lock")
    assert not hasattr(supervisor, "_release_lifecycle_lock")


def test_builder_and_supervisor_share_atomic_json_writer():
    assert builder.atomic_json is state_io.atomic_json
    assert supervisor.atomic is state_io.atomic_json


def test_shared_lifecycle_mutation_preserves_concurrent_records(tmp_path):
    path = tmp_path / "hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants": {
        "a": {"state": "CLAIMED"}, "b": {"state": "CLAIMED"}
    }}), encoding="utf-8")
    barrier = threading.Barrier(2)
    results = []

    def update(grant_id, marker):
        barrier.wait()
        results.append(lifecycle.mutate_grant_lifecycle(
            path, grant_id,
            lambda record: (record.__setitem__("marker", marker) or True),
        ))

    first = threading.Thread(target=update, args=("a", "A"))
    second = threading.Thread(target=update, args=("b", "B"))
    first.start(); second.start(); first.join(); second.join()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert all(results)
    assert saved["grants"]["a"]["marker"] == "A"
    assert saved["grants"]["b"]["marker"] == "B"


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"state": "ISSUED"}, "QUEUED"),
        ({"state": "DEFERRED", "result_status": "RUNNER_CAPACITY_DEFERRED"}, "QUEUED"),
        ({"state": "CLAIMED"}, "CLAIMED"),
        ({"state": "CLAIMED", "execution_stage": "BASE_VERIFIED"}, "PREPARING"),
        ({"state": "CLAIMED", "execution_stage": "BRANCH_CREATED"}, "PREPARING"),
        ({"state": "CLAIMED", "execution_stage": "EXECUTING"}, "IMPLEMENTING"),
        ({"state": "RUNNING"}, "IMPLEMENTING"),
        ({"state": "CLAIMED", "execution_stage": "EXECUTION_FINISHED"}, "VERIFYING"),
        ({"state": "CLAIMED", "execution_stage": "REPAIR_FINISHED"}, "VERIFYING"),
        ({"state": "ABANDONED", "execution_stage": "COMMITTED"}, "ABANDONED"),
        ({"state": "COMPLETED", "execution_stage": "PR_OPEN", "result_status": "PR_OPEN"}, "PR_OPEN"),
        ({"state": "COMPLETED", "result_status": "CI_PENDING"}, "CI"),
        ({"state": "COMPLETED", "result_status": "REVIEW_BLOCKED"}, "REVIEW"),
        ({"state": "CLAIMED", "execution_stage": "PUSHED"}, "PROMOTING"),
        ({"state": "COMPLETED", "result_status": "GREEN"}, "PROMOTING"),
        ({"state": "COMPLETED", "result_status": "NO_OP"}, "COMPLETE"),
        ({"state": "COMPLETED", "outcome_quality": "MERGED_VERIFIED"}, "COMPLETE"),
        ({"state": "FAILED", "result_status": "PREFLIGHT_FAILED"}, "FAILED"),
        ({"state": "ABANDONED"}, "ABANDONED"),
        ({"state": "QUARANTINED"}, "BLOCKED"),
        ({"state": "COMPLETED", "result_status": "MYSTERY"}, "UNKNOWN"),
        ({"state": "ALIEN"}, "UNKNOWN"),
    ],
)
def test_canonical_job_projection_maps_legacy_combinations_fail_closed(record, expected):
    projection = lifecycle.canonical_job_projection(record)

    assert projection["job_state"] == expected
    assert projection["lifecycle_state"] == record.get("state")
    assert projection["execution_stage"] == record.get("execution_stage")
    assert projection["result_status"] == record.get("result_status")
    assert projection["outcome_quality"] == record.get("outcome_quality")


def test_canonical_job_projection_rejects_non_record_input():
    with pytest.raises(TypeError, match="grant record"):
        lifecycle.canonical_job_projection(None)


def test_shared_stage_authority_rejects_unknown_normal_stage(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"CLAIMED"}}}),encoding="utf-8")

    assert lifecycle.advance_claimed_execution_stage(
        path,
        "g",
        "BUILDING",
        evidence={"head_sha":"abc"},
        updated_at="2026-09-27T00:00:00+00:00",
    ) is False

    assert "execution_stage" not in json.loads(path.read_text())["grants"]["g"]


def test_shared_stage_authority_persists_only_normal_claimed_stage(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"CLAIMED"}}}),encoding="utf-8")

    assert lifecycle.advance_claimed_execution_stage(
        path,
        "g",
        "BASE_VERIFIED",
        evidence={"head_sha":"abc","empty":"","none":None},
        updated_at="2026-09-27T00:00:00+00:00",
    )

    record=json.loads(path.read_text())["grants"]["g"]
    assert record["execution_stage"]=="BASE_VERIFIED"
    assert record["stage_updated_at"]=="2026-09-27T00:00:00+00:00"
    assert record["head_sha"]=="abc"
    assert "empty" not in record and "none" not in record


def test_shared_stage_authority_refuses_nonclaimed_grant(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"COMPLETED"}}}),encoding="utf-8")

    assert lifecycle.advance_claimed_execution_stage(
        path,
        "g",
        "PR_OPEN",
        evidence={},
        updated_at="2026-09-27T00:00:00+00:00",
    ) is False


def test_normal_stage_sequence_is_monotonic_with_optional_repair(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"CLAIMED"}}}),encoding="utf-8")
    stamp="2026-09-27T00:00:00+00:00"

    for stage in (
        "BASE_VERIFIED",
        "BRANCH_CREATED",
        "EXECUTING",
        "EXECUTION_FINISHED",
        "REPAIR_FINISHED",
        "PUSHED",
        "PR_OPEN",
    ):
        assert lifecycle.advance_claimed_execution_stage(
            path,"g",stage,evidence={},updated_at=stamp
        )

    assert json.loads(path.read_text())["grants"]["g"]["execution_stage"]=="PR_OPEN"


def test_normal_stage_sequence_allows_skipping_optional_repair(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{
        "state":"CLAIMED","execution_stage":"EXECUTION_FINISHED"
    }}}),encoding="utf-8")

    assert lifecycle.advance_claimed_execution_stage(
        path,"g","PUSHED",evidence={},updated_at="2026-09-27T00:00:00+00:00"
    )


def test_normal_stage_sequence_rejects_skip_and_regression(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{
        "state":"CLAIMED","execution_stage":"BRANCH_CREATED"
    }}}),encoding="utf-8")
    stamp="2026-09-27T00:00:00+00:00"

    assert lifecycle.advance_claimed_execution_stage(
        path,"g","PUSHED",evidence={},updated_at=stamp
    ) is False
    assert lifecycle.advance_claimed_execution_stage(
        path,"g","BASE_VERIFIED",evidence={},updated_at=stamp
    ) is False
    assert json.loads(path.read_text())["grants"]["g"]["execution_stage"]=="BRANCH_CREATED"


def test_normal_stage_sequence_same_stage_is_idempotent(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{
        "state":"CLAIMED","execution_stage":"EXECUTING"
    }}}),encoding="utf-8")

    assert lifecycle.advance_claimed_execution_stage(
        path,
        "g",
        "EXECUTING",
        evidence={"last_heartbeat_at":"new"},
        updated_at="2026-09-27T00:00:00+00:00",
    )
    assert json.loads(path.read_text())["grants"]["g"]["last_heartbeat_at"]=="new"


def test_candidate_verification_evidence_is_separate_from_execution_stage(tmp_path):
    grants=tmp_path/"hands-off-execution-grants"; grants.mkdir()
    grant=grants/"g.json"
    grant.write_text(json.dumps({"grant_id":"g"}),encoding="utf-8")
    lifecycle_path=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle_path.write_text(json.dumps({"grants":{"g":{
        "state":"CLAIMED","execution_stage":"EXECUTION_FINISHED"
    }}}),encoding="utf-8")

    assert lifecycle.record_claimed_candidate_verification(
        grant,
        repository="o/r",
        base_sha="base",
        branch="actor/x",
        head_sha="head-1",
        reviewer="deterministic-verdict-v2",
        preflight_evidence_binding={"head_sha":"head-1","evidence":"ok"},
        recorded_at_utc="2026-09-27T00:00:00+00:00",
    )

    record=json.loads(lifecycle_path.read_text())["grants"]["g"]
    assert record["execution_stage"]=="EXECUTION_FINISHED"
    assert record["candidate_verification"]["status"]=="APPROVED"
    assert record["candidate_verification"]["head_sha"]=="head-1"


def test_candidate_reverification_replaces_exact_head_after_rebase(tmp_path):
    grants=tmp_path/"hands-off-execution-grants"; grants.mkdir()
    grant=grants/"g.json"
    grant.write_text(json.dumps({"grant_id":"g"}),encoding="utf-8")
    lifecycle_path=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle_path.write_text(json.dumps({"grants":{"g":{
        "state":"CLAIMED","execution_stage":"EXECUTION_FINISHED"
    }}}),encoding="utf-8")

    common=dict(
        grant_path=grant,
        repository="o/r",
        branch="actor/x",
        reviewer="deterministic-verdict-v2",
    )
    assert lifecycle.record_claimed_candidate_verification(
        **common,
        base_sha="base-1",
        head_sha="head-1",
        preflight_evidence_binding={"head_sha":"head-1"},
        recorded_at_utc="2026-09-27T00:00:00+00:00",
    )
    assert lifecycle.record_claimed_candidate_verification(
        **common,
        base_sha="base-2",
        head_sha="head-2",
        preflight_evidence_binding={"head_sha":"head-2"},
        recorded_at_utc="2026-09-27T00:01:00+00:00",
    )

    record=json.loads(lifecycle_path.read_text())["grants"]["g"]
    assert record["execution_stage"]=="EXECUTION_FINISHED"
    assert record["candidate_verification"]["base_sha"]=="base-2"
    assert record["candidate_verification"]["head_sha"]=="head-2"
    assert record["candidate_verification"]["preflight_evidence_binding"]=={"head_sha":"head-2"}


def test_candidate_verification_evidence_refuses_terminal_grant(tmp_path):
    grants=tmp_path/"hands-off-execution-grants"; grants.mkdir()
    grant=grants/"g.json"
    grant.write_text(json.dumps({"grant_id":"g"}),encoding="utf-8")
    lifecycle_path=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle_path.write_text(json.dumps({"grants":{"g":{"state":"COMPLETED"}}}),encoding="utf-8")

    assert lifecycle.record_claimed_candidate_verification(
        grant,
        repository="o/r",
        base_sha="base",
        branch="actor/x",
        head_sha="head",
        reviewer="deterministic-verdict-v2",
        preflight_evidence_binding={"head_sha":"head"},
        recorded_at_utc="2026-09-27T00:00:00+00:00",
    ) is False
