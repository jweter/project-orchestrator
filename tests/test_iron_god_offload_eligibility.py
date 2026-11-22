"""Read-only provenance gate: an offload candidate needs three matching records."""
from __future__ import annotations

import json
import sqlite3

import pytest

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_development_memory as memory
from project_orchestrator import iron_god_offload_eligibility as eligibility
from project_orchestrator import iron_god_product_lane as product_lane
from project_orchestrator import iron_god_roadmap_refill as roadmap_refill

REPO = "jweter/VoiceStudio"
BASE = "a" * 40
OTHER = "b" * 40


@pytest.fixture
def source(tmp_path):
    release = tmp_path / "release"
    lane = tmp_path / "lane"
    release.joinpath("engineering", "state").mkdir(parents=True)
    lane.joinpath("packets").mkdir(parents=True)
    item = {
        "id": "VS-FAILED-1", "repository": REPO, "status": "approved",
        "assignee": backlog.AGENT_ID, "level": 1, "title": "Fix unit",
        "roadmap_ref": "docs/OWNER_APPROVED_ROADMAP_2026-10-25.md",
        "addresses": "G03", "why": "Existing tested issue",
        "consumer": "VoiceStudio frontend", "objective": "Implement the unit",
        "verification": "Native pytest", "definition_of_done": "Passes native tests",
        "complexity": "small", "interfaces": ["src/unit.py"],
        "depends_on": [], "acceptance_criteria": ["Unit behaves correctly"],
        "tests": ["tests/test_unit.py"],
        "build": {
            "module_path": "src/unit.py", "function_name": "unit",
            "signature": "unit() -> int", "objective": "Return the correct result",
            "examples": [{"call": "unit()", "expected": 2}],
            "test_path": "tests/test_unit.py",
        },
    }
    assert backlog.item_problems(item) == []
    (release / backlog.BACKLOG_PATH).write_text(
        json.dumps({"items": [item]}), encoding="utf-8",
    )
    (lane / "backlog-state.json").write_text(json.dumps({
        "schema_version": 1, "items": {
            item["id"]: {
                "outcome": "FAIL: SLICE_TESTS_FAILED",
                "packets": ["P-1"], "attempts": 3,
                "test_failed_packets": ["P-1"],
            },
        },
    }), encoding="utf-8")
    packet = {
        "packet_id": "P-1", "repository": REPO,
        "base_sha": BASE, "slice": item,
    }
    (lane / "packets" / "P-1.json").write_text(json.dumps(packet), encoding="utf-8")
    memory.record_attempt(
        lane, packet,
        {"status": "FAIL", "summary": "SLICE_TESTS_FAILED",
         "plan": {"mode": "build", "module_path": "src/unit.py"}},
        "FAIL: SLICE_TESTS_FAILED",
    )
    return release, lane, item, packet


def inspect(source, **changes):
    release, lane, _, _ = source
    params = {
        "observed_remote_head": BASE,
        "measured_local_level": 1,
        "open_pr_paths": [],
        "active_packet_id": None,
    }
    params.update(changes)
    return eligibility.inspect_difficult_slice(release, lane, REPO, **params)


def test_real_backlog_packet_sqlite_failure_yields_advisory_only_candidate(source):
    value = inspect(source)
    assert value["status"] == "CANDIDATE_UNVERIFIED"
    assert value["reason"] == "ATTEMPTS_EXHAUSTED"
    assert value["source"] == "APPROVED_BACKLOG_AND_EXISTING_FAILED_PACKET_SQLITE"
    assert value["execution_authorized"] is False
    assert value["can_claim_worker"] is False
    assert value["candidate"]["repository"] == REPO
    assert value["candidate"]["base_sha"] == BASE
    assert value["candidate"]["allowed_paths"] == ["src/unit.py", "tests/test_unit.py"]
    assert value["candidate"]["failure_ref"] == "packet:P-1"
    assert "CURRENT_AUTHENTICATED_OWNER_SESSION" in value["requires"]


def test_live_sha_drift_and_active_worker_do_not_yield_candidate(source):
    assert inspect(source, observed_remote_head=OTHER)["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"
    assert inspect(source, active_packet_id="P-LIVE")["reason"] == "ACTIVE_PRODUCT_PACKET_OWNS_LANE"
    assert inspect(source, open_pr_paths=["src/unit.py"])["reason"] == "NO_EXACT_HEAD_CAPABILITY_FAILURE"
    assert inspect(source, observed_remote_head="not-a-sha")["reason"] == "EXACT_LIVE_GIT_HEAD_REQUIRED"


@pytest.mark.parametrize("in_flight", sorted(backlog.IN_FLIGHT))
def test_canonical_same_agent_file_reservation_blocks_offload(source, in_flight):
    release, lane, item, _ = source
    other = json.loads(json.dumps(item))
    other["id"] = "VS-ACTIVE-2"
    other["build"]["module_path"] = "src/second.py"
    other["build"]["test_path"] = "tests/test_second.py"
    # Different build paths, but an existing interface reserves the exact
    # candidate module. The caller falsely reports no active packet.
    other["interfaces"] = ["src/unit.py"]
    assert backlog.item_problems(other) == []
    (release / backlog.BACKLOG_PATH).write_text(
        json.dumps({"items": [item, other]}), encoding="utf-8",
    )
    state_path = lane / "backlog-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["items"][other["id"]] = {
        "outcome": in_flight, "packets": ["P-2"], "attempts": 1,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    reservations = backlog.ownership([item, other], state, repository=REPO)
    assert any(entry["id"] == other["id"] and entry["reservation"]
               for entry in reservations)
    result = inspect(source, active_packet_id=None, open_pr_paths=[])
    assert result["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"
    assert result["execution_authorized"] is False

    # A reservation of genuinely unrelated paths must not suppress a
    # documented failure on different source/test files.
    other["interfaces"] = ["src/second.py"]
    (release / backlog.BACKLOG_PATH).write_text(
        json.dumps({"items": [item, other]}), encoding="utf-8",
    )
    assert inspect(source)["status"] == "CANDIDATE_UNVERIFIED"


@pytest.mark.parametrize("in_flight", sorted(backlog.IN_FLIGHT))
def test_runtime_admitted_file_reservation_blocks_static_offload(source, in_flight):
    release, lane, item, _ = source
    runtime = json.loads(json.dumps(item))
    runtime["id"] = "RM-VS-ADMITTED-1"
    runtime["origin"] = roadmap_refill.ORIGIN
    runtime["authorization"] = {"declared_capability_level": 1}
    runtime["build"]["module_path"] = "src/runtime.py"
    runtime["build"]["test_path"] = "tests/test_runtime.py"
    runtime["interfaces"] = ["src/unit.py"]
    assert backlog.item_problems(runtime) == []
    (lane / roadmap_refill.STORE).write_text(json.dumps({"items": [runtime]}), encoding="utf-8")
    assert [x["id"] for x in roadmap_refill.admitted_items(lane, [item])] == [runtime["id"]]
    state_file = lane / "backlog-state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["items"][runtime["id"]] = {
        "outcome": in_flight, "packets": ["RUNTIME-P-2"], "attempts": 1,
    }
    state_file.write_text(json.dumps(state), encoding="utf-8")
    assert backlog.ownership([item, runtime], state, repository=REPO)[1]["reservation"]
    assert inspect(source, active_packet_id=None, open_pr_paths=[])["status"] == (
        "NO_VERIFIED_OFFLOAD_CANDIDATE"
    )
    runtime["interfaces"] = ["src/runtime.py"]
    (lane / roadmap_refill.STORE).write_text(json.dumps({"items": [runtime]}), encoding="utf-8")
    assert inspect(source)["status"] == "CANDIDATE_UNVERIFIED"


def _admit_failed_runtime_slice(source):
    """Provide the same approved runtime item in roadmap, packet, and SQLite."""
    release, lane, item, _ = source
    static_item = json.loads(json.dumps(item))
    static_item["status"] = "proposed"
    (release / backlog.BACKLOG_PATH).write_text(
        json.dumps({"items": [static_item]}), encoding="utf-8",
    )
    runtime = json.loads(json.dumps(item))
    runtime["id"] = "RM-VS-RUNTIME-FAILED-1"
    runtime["origin"] = roadmap_refill.ORIGIN
    runtime["authorization"] = {"declared_capability_level": 1}
    runtime["build"]["module_path"] = "src/runtime_unit.py"
    runtime["build"]["test_path"] = "tests/test_runtime_unit.py"
    runtime["interfaces"] = ["src/runtime_unit.py"]
    assert backlog.item_problems(runtime) == []
    store = lane / roadmap_refill.STORE
    store.write_text(json.dumps({"schema_version": 1, "items": [runtime]}), encoding="utf-8")
    state_path = lane / "backlog-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["items"][runtime["id"]] = {
        "outcome": "FAIL: SLICE_TESTS_FAILED", "attempts": 3,
        "packets": ["RUNTIME-P-1"], "test_failed_packets": ["RUNTIME-P-1"],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    # A real product worker packet intentionally contains only an execution
    # snapshot, not the entire mutable runtime backlog row. Exercise that
    # producer contract directly (previous fixture accidentally used runtime).
    packet = product_lane.build_product_packet(
        REPO, packet_id="RUNTIME-P-1", base=BASE,
        worktree=lane / "worker", branch="test-runtime-offload",
        roots=["src/runtime_unit.py", "tests/test_runtime_unit.py"],
        avoid=[], memory_text="", project="VoiceStudio", item=runtime,
    )
    assert packet["slice"]["origin"] == roadmap_refill.ORIGIN
    assert packet["slice"]["authorization"] == runtime["authorization"]
    assert packet["slice"]["authorization"] is not runtime["authorization"]
    assert packet["slice"]["status"] == "approved"
    assert packet["slice"]["assignee"] == backlog.AGENT_ID
    assert "interfaces" not in packet["slice"]
    (lane / "packets" / "RUNTIME-P-1.json").write_text(
        json.dumps(packet), encoding="utf-8",
    )
    memory.record_attempt(
        lane, packet,
        {"status": "FAIL", "summary": "SLICE_TESTS_FAILED",
         "plan": {"mode": "build", "module_path": "src/runtime_unit.py"}},
        "FAIL: SLICE_TESTS_FAILED",
    )
    return runtime


def test_verified_runtime_admission_can_supply_advisory_offload_candidate(source):
    runtime = _admit_failed_runtime_slice(source)
    found = inspect(source)
    assert found["status"] == "CANDIDATE_UNVERIFIED"
    assert found["source"] == "RUNTIME_ADMITTED_ROADMAP_AND_FAILED_PACKET_SQLITE"
    assert found["candidate"]["slice_id"] == runtime["id"]
    assert found["candidate"]["allowed_paths"] == [
        "src/runtime_unit.py", "tests/test_runtime_unit.py",
    ]
    assert found["candidate"]["failure_ref"] == "packet:RUNTIME-P-1"
    assert found["execution_authorized"] is False
    assert found["can_claim_worker"] is False


def test_runtime_only_backlog_still_yields_inert_candidate(source):
    runtime = _admit_failed_runtime_slice(source)
    release, _, _, _ = source
    (release / backlog.BACKLOG_PATH).write_text(
        json.dumps({"items": []}), encoding="utf-8",
    )
    found = inspect(source)
    assert found["status"] == "CANDIDATE_UNVERIFIED"
    assert found["candidate"]["slice_id"] == runtime["id"]
    assert found["execution_authorized"] is False


def test_runtime_candidate_needs_actual_implementation_failure(source):
    runtime = _admit_failed_runtime_slice(source)
    _, lane, _, _ = source
    with sqlite3.connect(memory.db_path(lane)) as db:
        db.execute(
            "UPDATE attempts SET failure_class='ENVIRONMENT' WHERE packet_id=?",
            ("RUNTIME-P-1",),
        )
    assert inspect(source)["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"
    assert runtime["id"] not in str(inspect(source).get("candidate", {}))


def test_runtime_candidate_requires_unchanged_packet_admission_provenance(source):
    runtime = _admit_failed_runtime_slice(source)
    _, lane, _, _ = source
    runtime["authorization"]["owner_attestation"] = "new-unverified-claim"
    (lane / roadmap_refill.STORE).write_text(
        json.dumps({"items": [runtime]}), encoding="utf-8",
    )
    assert inspect(source)["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"


def test_unknown_canonical_inflight_owner_prevents_runtime_offload(source):
    _admit_failed_runtime_slice(source)
    _, lane, _, _ = source
    state_path = lane / "backlog-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["items"]["RM-UNKNOWN-RESERVATION"] = {
        "outcome": "DISPATCHED", "packets": ["UNRECONCILED-P-1"], "attempts": 1,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert inspect(source)["reason"] == "UNKNOWN_CANONICAL_IN_FLIGHT_OWNER"


def test_corrupted_runtime_store_fails_closed(source):
    _, lane, _, _ = source
    (lane / roadmap_refill.STORE).write_text("invalid json", encoding="utf-8")
    assert inspect(source)["reason"] == "RUNTIME_BACKLOG_STORE_INVALID"


def test_corrupted_memory_cannot_mint_candidate(source):
    _, lane, _, _ = source
    with sqlite3.connect(memory.db_path(lane)) as db:
        db.execute("DELETE FROM attempts")
    assert inspect(source)["reason"] == "NO_EXACT_HEAD_CAPABILITY_FAILURE"
    assert not (lane / "coordination.sqlite").exists()


def test_packet_mismatch_or_missing_is_not_an_authority(source):
    _, lane, _, _ = source
    path = lane / "packets" / "P-1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["base_sha"] = OTHER
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert inspect(source)["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"
    path.unlink()
    assert inspect(source)["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"


def test_live_takeover_or_completed_or_unapproved_work_is_excluded(source):
    release, lane, item, _ = source
    path = lane / "backlog-state.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["items"][item["id"]]["outcome"] = "DISPATCHED"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert inspect(source)["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"

    data["items"][item["id"]]["outcome"] = "MERGED"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert inspect(source)["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"

    data["items"][item["id"]]["outcome"] = "FAIL: SLICE_TESTS_FAILED"
    path.write_text(json.dumps(data), encoding="utf-8")
    item["status"] = "proposed"
    (release / backlog.BACKLOG_PATH).write_text(
        json.dumps({"items": [item]}), encoding="utf-8",
    )
    assert inspect(source)["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"


def test_dependency_conflict_and_missing_owner_evidence(source):
    release, lane, item, _ = source
    item["depends_on"] = ["VS-UNMERGED"]
    (release / backlog.BACKLOG_PATH).write_text(json.dumps({"items": [item]}), encoding="utf-8")
    assert inspect(source)["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"

    (lane / "backlog-state.json").unlink()
    assert inspect(source)["reason"] == "CANONICAL_BACKLOG_STATE_MISSING"


def test_rejected_backlog_duplicate_id_fails_closed(source):
    release, _, item, _ = source
    (release / backlog.BACKLOG_PATH).write_text(
        json.dumps({"items": [item, item]}), encoding="utf-8",
    )
    assert inspect(source)["reason"] == "BACKLOG_REJECTED_ITEMS_PRESENT"


def test_rewritten_build_and_interface_cannot_inherit_old_failure(source):
    release, _, item, _ = source
    item["interfaces"] = ["src/unrelated.py"]
    item["build"]["module_path"] = "src/unrelated.py"
    (release / backlog.BACKLOG_PATH).write_text(
        json.dumps({"items": [item]}), encoding="utf-8",
    )
    assert inspect(source)["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"


def test_unrecorded_interface_cannot_be_silently_added_to_failure_scope(source):
    release, _, item, _ = source
    item["interfaces"].append("src/extra.py")
    (release / backlog.BACKLOG_PATH).write_text(
        json.dumps({"items": [item]}), encoding="utf-8",
    )
    assert inspect(source)["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"


@pytest.mark.parametrize("tamper", [
    None, {"attempts": "three", "packets": ["P-1"], "outcome": "FAIL: SLICE_TESTS_FAILED"},
    {"attempts": -1, "packets": ["P-1"], "outcome": "FAIL: SLICE_TESTS_FAILED"},
    {"attempts": 3, "packets": "P-1", "outcome": "FAIL: SLICE_TESTS_FAILED"},
])
def test_malformed_backlog_ledger_fails_closed_before_helpers(source, tamper):
    _, lane, item, _ = source
    path = lane / "backlog-state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["items"][item["id"]] = tamper
    path.write_text(json.dumps(state), encoding="utf-8")
    assert inspect(source)["reason"] == "BACKLOG_STATE_INVALID"


@pytest.mark.parametrize("failure_class", [
    "TEST_NOT_DISCRIMINATING", "NEEDS_REVALIDATION", "ENVIRONMENT",
    "COMPLETED_EXTERNALLY", "BLOCKED_CONFLICT", "PASSED", "OTHER",
])
def test_nonimplementation_failures_never_count_as_local_capability_failures(source, failure_class):
    _, lane, _, _ = source
    # The canonical backlog still says FAIL and its directed attempts are
    # exhausted, but these records do not prove the model failed at coding.
    with sqlite3.connect(memory.db_path(lane)) as db:
        db.execute(
            "UPDATE attempts SET failure_class=? WHERE packet_id=?",
            (failure_class, "P-1"),
        )
    result = inspect(source)
    assert result["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"
    assert result["reason"] == "NO_EXACT_HEAD_CAPABILITY_FAILURE"


@pytest.mark.parametrize("failure_class", [
    "SLICE_TESTS_FAILED", "REPLY_NO_CODE", "STATIC_MYPY",
])
def test_recorded_implementation_failures_are_still_eligible(source, failure_class):
    _, lane, _, _ = source
    with sqlite3.connect(memory.db_path(lane)) as db:
        db.execute(
            "UPDATE attempts SET failure_class=? WHERE packet_id=?",
            (failure_class, "P-1"),
        )
    result = inspect(source)
    assert result["status"] == "CANDIDATE_UNVERIFIED"
    assert result["execution_authorized"] is False


def test_actual_legacy_static_product_packet_does_not_forge_runtime_origin(source):
    _, lane, item, _ = source
    packet = product_lane.build_product_packet(
        REPO, packet_id="STATIC-P-1", base=BASE,
        worktree=lane / "static-worker", branch="static-test",
        roots=["src/unit.py", "tests/test_unit.py"],
        avoid=[], memory_text="", project="VoiceStudio", item=item,
    )
    assert packet["slice"]["id"] == item["id"]
    assert "origin" not in packet["slice"]
    assert "authorization" not in packet["slice"]
    assert "status" not in packet["slice"]
    assert "assignee" not in packet["slice"]


def test_missing_production_runtime_admission_snapshot_is_not_inferred(source):
    _admit_failed_runtime_slice(source)
    _, lane, _, _ = source
    packet_path = lane / "packets" / "RUNTIME-P-1.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["slice"].pop("origin")
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    assert inspect(source)["status"] == "NO_VERIFIED_OFFLOAD_CANDIDATE"
