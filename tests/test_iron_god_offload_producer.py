"""Exact-head, current-owner, path-lease gates for inert offload producer."""
from __future__ import annotations

import json
import sqlite3

import pytest

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_development_memory as memory
from project_orchestrator import iron_god_offload as offload
from project_orchestrator import iron_god_offload_producer as producer

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
        "schema_version": 1,
        "items": {
            item["id"]: {
                "outcome": "FAIL: SLICE_TESTS_FAILED",
                "packets": ["P-1"], "attempts": 3,
                "test_failed_packets": ["P-1"],
            },
        },
    }), encoding="utf-8")
    packet = {"packet_id": "P-1", "repository": REPO,
              "base_sha": BASE, "slice": item}
    (lane / "packets" / "P-1.json").write_text(
        json.dumps(packet), encoding="utf-8",
    )
    memory.record_attempt(
        lane, packet,
        {"status": "FAIL", "summary": "SLICE_TESTS_FAILED",
         "plan": {"mode": "build", "module_path": "src/unit.py"}},
        "FAIL: SLICE_TESTS_FAILED",
    )
    return release, lane, tmp_path / "advisory.sqlite"


def live(head=BASE, *, prs=None, packet=None, level=1):
    return {
        "observed_remote_head": head,
        "measured_local_level": level,
        "open_pr_paths": [] if prs is None else list(prs),
        "active_packet_id": packet,
    }


def publish(source, *, read_live=None, owner=None, conflict=None,
            enabled=True):
    release, lane, db = source
    return producer.offer_verified_difficult_slice(
        db_path=db, release_repo=release, lane_root=lane, repository=REPO,
        read_live=(lambda _: live()) if read_live is None else read_live,
        authenticated_owner_check=(lambda: True) if owner is None else owner,
        no_conflicting_lease_check=(
            lambda _repo, _paths: True
        ) if conflict is None else conflict,
        advisory_writes_enabled=enabled,
    )


def test_current_head_failed_worker_and_verified_gates_publish_inert_offer(source):
    _, _, db = source
    paths = []

    def check_claim(repo, files):
        paths.append((repo, files))
        return True

    value = publish(source, conflict=check_claim)
    assert value["status"] == "ADVISORY_OFFER_PUBLISHED"
    assert value["base_sha"] == BASE
    assert value["source"] == "APPROVED_BACKLOG_AND_EXISTING_FAILED_PACKET_SQLITE"
    assert value["allowed_paths"] == ["src/unit.py", "tests/test_unit.py"]
    assert value["execution_authorized"] is False
    assert value["worker_lease_granted"] is False
    assert value["native_acceptance_verified"] is False
    assert value["requires_current_owner_lease_and_head_revalidation"] is True
    assert len(paths) == 3 and all(p[0] == REPO for p in paths)
    assert all(p[1] == ("src/unit.py", "tests/test_unit.py") for p in paths)
    assert offload.read_offer(db, value["offer_id"])["base_sha"] == BASE

    replay = publish(source)
    assert replay["status"] == "ADVISORY_OFFER_PUBLISHED"
    assert replay["offer_id"] == value["offer_id"]
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM offload_offers").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM offload_claims").fetchone()[0] == 0


@pytest.mark.parametrize("kwargs,reason", [
    ({"enabled": False}, "ADVISORY_WRITES_NOT_AUTHORIZED"),
    ({"owner": lambda: False}, "OWNER_NOT_AUTHORIZED"),
    ({"conflict": lambda _repo, _files: False}, "WORKER_OR_PATH_CLAIM_CONFLICT"),
    ({"read_live": lambda _: live(OTHER)}, "NO_VERIFIED_CURRENT_HEAD_FAILURE"),
    ({"read_live": lambda _: live(prs=["src/unit.py"])},
     "NO_VERIFIED_CURRENT_HEAD_FAILURE"),
    ({"read_live": lambda _: live(packet="P-LIVE")},
     "NO_VERIFIED_CURRENT_HEAD_FAILURE"),
    ({"read_live": lambda _: {"observed_remote_head": BASE}},
     "LIVE_EVIDENCE_OR_OFFER_UNAVAILABLE"),
    ({"read_live": lambda _: live(head="bad")},
     "LIVE_EVIDENCE_OR_OFFER_UNAVAILABLE"),
    ({"read_live": lambda _: {**live(), "fake_owner_grant": True}},
     "LIVE_EVIDENCE_OR_OFFER_UNAVAILABLE"),
])
def test_denied_or_unproven_context_cannot_write_offer(source, kwargs, reason):
    _, _, db = source
    value = publish(source, **kwargs)
    assert value["status"] == "OFFER_REFUSED"
    assert value["reason"] == reason
    assert value["execution_authorized"] is False
    assert not db.exists()


def test_recheck_current_head_drift_blocks_before_mutation(source):
    _, _, db = source
    observations = iter([live(), live(OTHER)])

    def next_view(_repo):
        return next(observations)

    value = publish(source, read_live=next_view)
    assert value["status"] == "OFFER_REFUSED"
    assert value["reason"] == "LIVE_GATES_CHANGED_BEFORE_OFFER"
    assert not db.exists()


def test_revoked_owner_grant_before_write_blocks_without_offer(source):
    _, _, db = source
    count = 0

    def owner():
        nonlocal count
        count += 1
        return count == 1

    result = publish(source, owner=owner)
    assert result["reason"] == "LIVE_GATES_CHANGED_BEFORE_OFFER"
    assert not db.exists()


def test_revocation_after_immutable_offer_does_not_claim_success(source):
    _, _, db = source
    count = 0

    def owner():
        nonlocal count
        count += 1
        return count <= 2

    result = publish(source, owner=owner)
    assert result["status"] == "OFFER_REFUSED"
    assert result["reason"] == "OFFER_REQUIRES_REVALIDATION_AFTER_GATE_CHANGE"
    assert result["worker_lease_granted"] is False
    assert db.exists()
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM offload_offers").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM offload_claims").fetchone()[0] == 0


def test_missing_or_faulted_witness_callbacks_fail_closed(source):
    _, _, db = source
    release, lane, _ = source
    missing = producer.offer_verified_difficult_slice(
        db_path=db, release_repo=release, lane_root=lane,
        repository=REPO, read_live=None,
        authenticated_owner_check=lambda: True,
        no_conflicting_lease_check=lambda _r, _p: True,
        advisory_writes_enabled=True,
    )
    assert missing["reason"] == "INDEPENDENT_LIVE_WITNESSES_REQUIRED"
    assert not db.exists()

    def fault(_repo):
        raise RuntimeError("PRIVATE_KEY=secret")

    result = publish(source, read_live=fault)
    assert result["reason"] == "LIVE_WITNESS_OR_ORACLE_FAILED"
    assert "secret" not in str(result)
    assert not db.exists()


def test_only_explicit_absolute_host_paths_can_write(source):
    release, lane, db = source
    for supplied_db, supplied_release, supplied_lane in (
        (db.relative_to(db.parent), release, lane),
        (db, release.relative_to(release.parent), lane),
        (db, release, lane.relative_to(lane.parent)),
        (db.parent / "not-a-folder" / "advisory.sqlite", release, lane),
    ):
        result = producer.offer_verified_difficult_slice(
            db_path=supplied_db, release_repo=supplied_release,
            lane_root=supplied_lane, repository=REPO,
            read_live=lambda _: live(),
            authenticated_owner_check=lambda: True,
            no_conflicting_lease_check=lambda _repo, _files: True,
            advisory_writes_enabled=True,
        )
        assert result["reason"] == "CONFIGURED_HOST_ROOTS_INVALID"
        assert result["execution_authorized"] is False
    assert not db.exists()


def test_canonical_spec_changed_during_first_lease_check_refuses_stale_offer(source):
    release, _, db = source
    backlog_path = release / backlog.BACKLOG_PATH
    calls = 0

    def mutate_during_claim(_repository, _files):
        nonlocal calls
        calls += 1
        if calls == 1:
            record = json.loads(backlog_path.read_text(encoding="utf-8"))
            record["items"][0]["build"]["objective"] = "New owner-approved specification"
            backlog_path.write_text(json.dumps(record), encoding="utf-8")
        return True

    result = publish(source, conflict=mutate_during_claim)
    assert result["reason"] == "CANDIDATE_EVIDENCE_CHANGED_BEFORE_OFFER"
    assert result["status"] == "OFFER_REFUSED"
    assert not db.exists()


def test_canonical_failure_state_changed_before_offer_refuses(source):
    _, lane, db = source
    state_path = lane / "backlog-state.json"
    calls = 0

    def mutate_during_claim(_repository, _files):
        nonlocal calls
        calls += 1
        if calls == 1:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["items"]["VS-FAILED-1"]["outcome"] = "MERGED"
            state_path.write_text(json.dumps(state), encoding="utf-8")
        return True

    result = publish(source, conflict=mutate_during_claim)
    assert result["reason"] == "CANDIDATE_EVIDENCE_CHANGED_BEFORE_OFFER"
    assert not db.exists()


def test_post_publication_canonical_failure_revocation_cannot_approve_offer(source, monkeypatch):
    _, lane, db = source
    state_path = lane / "backlog-state.json"
    publish_original = offload.offer_slice

    def publish_then_revoke(*args, **kwargs):
        offer = publish_original(*args, **kwargs)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["items"]["VS-FAILED-1"]["outcome"] = "MERGED"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        return offer

    monkeypatch.setattr(offload, "offer_slice", publish_then_revoke)
    result = publish(source)
    assert result["status"] == "OFFER_REFUSED"
    assert result["reason"] == "OFFER_REQUIRES_REVALIDATION_AFTER_GATE_CHANGE"
    assert result["worker_lease_granted"] is False
    assert db.exists()
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM offload_offers").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM offload_claims").fetchone()[0] == 0


def test_failed_packet_stale_at_current_git_head_never_becomes_offer(source):
    _, lane, db = source
    packet = lane / "packets" / "P-1.json"
    state = json.loads(packet.read_text(encoding="utf-8"))
    state["base_sha"] = OTHER
    packet.write_text(json.dumps(state), encoding="utf-8")
    result = publish(source)
    assert result["reason"] == "NO_VERIFIED_CURRENT_HEAD_FAILURE"
    assert not db.exists()
