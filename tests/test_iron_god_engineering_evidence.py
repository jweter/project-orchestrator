"""Regression tests for the read-only seven-repository Iron God evidence plane."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from project_orchestrator import iron_god_engineering_evidence as evidence
from project_orchestrator import iron_god_observability as obs


INSTANCE = "generation-xyz"
SHA = "a" * 40
REPOSITORY = "jweter/VoiceStudio"


def _put(root: Path, relative: str, data: dict) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data), encoding="utf-8")


def _state(root: Path, stamp: datetime) -> None:
    iso = stamp.isoformat()
    _put(root, "supervisor-generation-heartbeat.json", {
        "heartbeat_at_utc": iso, "status": "RUNNING_CURRENT",
        "process_instance_id": INSTANCE, "actor_revision": SHA,
    })
    _put(root, "supervisor-execution-authority.json", {
        "observed_at_utc": iso, "permitted": True,
        "supervisor_instance_id": INSTANCE,
    })
    _put(root, "supervisor.json", {
        "updated_at_utc": iso, "status": "FLOW_DEGRADED",
        "builder_status": "NO_READY_EXECUTION_SLICE",
        "authorized_products": [],
    })
    _put(root, "iron-god-canon/lane-status.json", {
        "observed_at_utc": iso, "status": "IDLE_NO_READY_SLICE",
    })
    _put(root, "iron-god-products/lane-status.json", {
        "observed_at_utc": iso, "status": "WAITING_FOR_MEMORY",
        "repository": REPOSITORY, "packet_id": "packet-123",
    })
    _put(root, "parallel-worker-pool.json", {
        "recorded_at_utc": iso, "pool": {"running_total": 0},
        "runtime_leases": {"leases": []},
    })
    _put(root, "worker-memory-admission.json", {
        "observed_at_utc": iso, "status": "CRITICAL_MEMORY_NO_NEW_WORK",
        "available_mib": 1700, "new_worker_budget": 0,
    })
    _put(root, "continuation-current-burst.json", {
        "updated_at_utc": iso, "process_instance_id": INSTANCE,
        "last_sequence": 3,
    })
    _put(root, "verified-delivery-throughput.json", {
        "status": "NO_AUTHORIZED_WORK", "verified_deliveries": 0,
        "product_reality_verified": 0,
    })


def _attempt_db(root: Path) -> Path:
    db_file = root / "iron-god-products" / "development-memory.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_file) as db:
        db.executescript("""
            CREATE TABLE attempts (
                packet_id TEXT PRIMARY KEY,item_id TEXT,repository TEXT,
                base_sha TEXT,module_path TEXT,targets TEXT,method TEXT,
                model TEXT,instructions TEXT,prompt TEXT,reply TEXT,tests TEXT,
                static TEXT,failure_class TEXT,outcome TEXT,recorded_at_utc TEXT);
            CREATE TABLE rules (
                failure_class TEXT PRIMARY KEY,rule TEXT,evidence TEXT,
                slices INTEGER,kind TEXT,derived_at_utc TEXT);
        """)
        db.execute("""INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "packet-123", "slice-1", REPOSITORY, SHA, "private.py", "[]", "build",
            "qwen2.5-coder:7b", "private instructions", "SECRET_API_KEY_XXXXX",
            "PRIVATE_REPLY", "restricted test output", "PRIVATE_STACK",
            "SLICE_TESTS_FAILED", "FAILED detail SECRET", datetime.now(UTC).isoformat(),
        ))
        db.execute("INSERT INTO rules VALUES (?,?,?,?,?,?)", (
            "SLICE_TESTS_FAILED", "SECRET_LESSON", "SECRET_PATH", 3,
            "VERIFIED_FROM_3_SLICES", datetime.now(UTC).isoformat(),
        ))
    return db_file


def test_evidence_bundle_reuses_durable_checkpoint_and_is_not_a_restart_grant(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    assert obs.record_supervisor_pass(tmp_path, now=now) is True

    bundle = evidence.engineering_bundle(tmp_path, repository=REPOSITORY, now=now)
    assert bundle["durable_continuation"]["source_status"] == "OK"
    previous = bundle["durable_continuation"]["last_observed_checkpoint"]
    assert previous["continuation_sequence"] == 3
    assert previous["source_revision"] == SHA
    assert previous["packet_id"] == "packet-123"
    assert previous["repository"] == REPOSITORY
    assert bundle["durable_continuation"]["resume_authorized"] is False
    assert bundle["frontier_and_recovery"]["live_status"] == "BLOCKED"
    assert bundle["frontier_and_recovery"]["reason"] == "PRODUCT_LANE_WAITING_FOR_MEMORY"
    assert bundle["frontier_and_recovery"]["advice"]["action"] == "WAIT_FOR_SAFE_MEMORY_ADMISSION"
    assert bundle["frontier_and_recovery"]["execution_authorized_by_bundle"] is False
    assert bundle["resource_admission"]["cloud_spend_authorized"] is False
    assert bundle["delivery"]["native_product_acceptance"] == "UNVERIFIED"
    assert bundle["delivery"]["ci"] == "UNVERIFIED"
    assert bundle["mcp_handoff"]["can_dispatch"] is False
    assert bundle["registered_repositories"] == list(obs.REPOSITORIES)
    assert bundle["evidence_graph"]["edges"][0]["source_event_seq"] == 1


def test_restart_uses_historical_evidence_but_never_resumes_stale_generation(tmp_path):
    at = datetime.now(UTC) - timedelta(minutes=40)
    _state(tmp_path, at)
    assert obs.record_supervisor_pass(tmp_path, now=at) is True

    bundle = evidence.engineering_bundle(tmp_path, now=at + timedelta(minutes=40))
    assert bundle["durable_continuation"]["last_observed_checkpoint"]["continuation_sequence"] == 3
    assert bundle["frontier_and_recovery"]["live_status"] == "UNVERIFIED"
    assert bundle["durable_continuation"]["resume_authorized"] is False
    assert bundle["supervisor"]["execution_permitted"] is True  # historical data, not an owner grant
    assert bundle["history"]["historical_only"] is True


def test_attempt_memory_never_exposes_prompts_replies_test_logs_or_unscreened_rules(tmp_path):
    _state(tmp_path, datetime.now(UTC))
    database = _attempt_db(tmp_path)
    before = database.stat().st_size
    bundle = evidence.engineering_bundle(tmp_path, repository=REPOSITORY)
    assert bundle["learning"]["status"] == "OK"
    assert bundle["learning"]["attempts"][0]["failure_class"] == "SLICE_TESTS_FAILED"
    assert bundle["learning"]["rules"][0]["independent_slices"] == 3
    assert bundle["learning"]["rules"][0]["authority"] == "ADVISORY_ONLY"
    assert "SECRET" not in json.dumps(bundle)
    assert "PRIVATE_" not in json.dumps(bundle)
    assert database.stat().st_size == before
    assert bundle["evidence_graph"]["inferred_dependencies"] == 0
    assert any(e["predicate"] == "ATTEMPT_REPORTED_CLASS"
               for e in bundle["evidence_graph"]["edges"])


def test_missing_databases_are_not_created_by_reader(tmp_path):
    _state(tmp_path, datetime.now(UTC))
    before = sorted(str(path.relative_to(tmp_path))
                    for path in tmp_path.rglob("*") if path.is_file())
    for _ in range(3):
        bundle = evidence.engineering_bundle(tmp_path)
        assert bundle["history"]["status"] == "UNAVAILABLE"
        assert bundle["learning"]["status"] == "UNAVAILABLE"
        assert bundle["durable_continuation"]["last_observed_checkpoint"] is None
    after = sorted(str(path.relative_to(tmp_path))
                   for path in tmp_path.rglob("*") if path.is_file())
    assert before == after


def test_scoped_query_does_not_infer_other_repository_has_no_work(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    obs.record_supervisor_pass(tmp_path, now=now)
    bundle = evidence.engineering_bundle(tmp_path, repository="jweter/Project-Everward", now=now)
    assert bundle["frontier_and_recovery"]["live_status"] == "UNVERIFIED_FOR_REPOSITORY"
    assert bundle["frontier_and_recovery"]["advice"] is None
    assert bundle["history"]["events"] == []


@pytest.mark.parametrize("name,limit", [
    ("jweter/unknown-repository", 8),
    ("../IronGodControl", 8),
    ("", 0),
    ("", 21),
    ("", True),
])
def test_unknown_scope_and_unbounded_requests_fail_closed(tmp_path, name, limit):
    with pytest.raises(ValueError):
        evidence.engineering_bundle(tmp_path, repository=name, limit=limit)


def test_corrupt_or_missing_sqlite_evidence_is_unverified_not_accepted(tmp_path):
    _state(tmp_path, datetime.now(UTC))
    target = tmp_path / "observability" / "development-events.sqlite"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"not-a-sqlite-file")
    bundle = evidence.engineering_bundle(tmp_path)
    assert bundle["history"]["status"] == "UNVERIFIED"
    assert bundle["durable_continuation"]["last_observed_checkpoint"] is None
    assert bundle["delivery"]["native_product_acceptance"] == "UNVERIFIED"


def test_fresh_existing_eligibility_evidence_reuses_actual_product_gate(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    _put(tmp_path, "iron-god-products/eligibility.json", {
        "observed_at_utc": now.isoformat(),
        "items": [
            {"id": "V1", "repository": REPOSITORY, "eligible": False,
             "reason": "ATTEMPTS_EXHAUSTED", "attempts": 3,
             "detail": "PRIVATE_SENSITIVE_WORKER_LOG"},
            {"id": "V2", "repository": REPOSITORY, "eligible": True,
             "reason": "ELIGIBLE", "attempts": 0},
            {"id": "E1", "repository": "jweter/Project-Everward", "eligible": False,
             "reason": "LEVEL_ABOVE_MEASURED_CAPABILITY", "attempts": 0},
        ],
    })
    bundle = evidence.engineering_bundle(tmp_path, repository=REPOSITORY, now=now)
    frontier = bundle["frontier_and_recovery"]["observed_candidate_ids"]
    assert frontier["status"] == "OBSERVED_FRESH"
    assert [row["id"] for row in frontier["candidates"]] == ["V1", "V2"]
    assert frontier["next_candidate"]["id"] == "V2"
    assert frontier["dispatch_authorized"] is False
    assert frontier["new_owner_grant"] is False
    assert frontier["model_qualified_for_candidate"] == "UNVERIFIED"
    assert "PRIVATE_SENSITIVE_WORKER_LOG" not in json.dumps(bundle)


def test_stale_eligibility_never_resurrects_eligible_slice(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    _put(tmp_path, "iron-god-products/eligibility.json", {
        "observed_at_utc": (now - timedelta(hours=2)).isoformat(),
        "items": [{"id": "V2", "repository": REPOSITORY, "eligible": True}],
    })
    result = evidence.frontier_evidence(tmp_path, now=now)
    assert result["status"] == "STALE"
    assert result["candidates"] == []
    assert result["next_candidate"] is None
    assert result["dispatch_authorized"] is False


def test_only_preexisting_eligible_frontier_may_be_reported_not_inferred(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    _put(tmp_path, "iron-god-products/eligibility.json", {
        "observed_at_utc": now.isoformat(),
        "items": [
            {"id": "X", "repository": REPOSITORY, "eligible": False,
             "reason": "NEEDS_DIFFERENT_CAPABILITY", "attempts": 2},
        ],
    })
    result = evidence.frontier_evidence(tmp_path, now=now)
    assert result["status"] == "OBSERVED_FRESH"
    assert result["next_candidate"] is None
    assert result["candidates"][0]["reason"] == "NEEDS_DIFFERENT_CAPABILITY"


def test_reject_undocumented_unknown_repository_even_when_in_frontier(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    _put(tmp_path, "iron-god-products/eligibility.json", {
        "observed_at_utc": now.isoformat(),
        "items": [{"id": "PRIVATE", "repository": "jweter/secret-repository",
                   "eligible": True}],
    })
    result = evidence.frontier_evidence(tmp_path, now=now)
    assert result["candidates"] == []
    assert result["next_candidate"] is None


def test_measured_resource_view_reuses_existing_model_calibration_not_cloud(tmp_path):
    from project_orchestrator import iron_god_calibration as calibration

    now = datetime.now(UTC)
    _state(tmp_path, now)
    _put(tmp_path, "worker-memory-admission.json", {
        "observed_at_utc": now.isoformat(), "status": "NORMAL",
        "available_mib": 8192, "new_worker_budget": 1,
    })
    profile_name = calibration.profile_path(tmp_path, "ollama/qwen2.5-coder:3b")
    _put(tmp_path, str(profile_name.relative_to(tmp_path)), {
        "schema_version": 1,
        "ladder_version": calibration.LADDER_VERSION,
        "model": "ollama/qwen2.5-coder:3b",
        "hardware": calibration.hardware_fingerprint(),
        "max_passing_level": 1,
    })
    route = evidence.measured_local_admission(tmp_path, now=now)
    assert route["status"] == "MEASURED_ADVISORY"
    assert route["model"] == "qwen2.5-coder:3b"
    assert route["qualified_level"] >= 1
    assert route["cloud_spend_authorized"] is False
    assert route["new_worker_authorized"] is False
    assert route["native_toolchain_qualification"] == "UNVERIFIED"


def test_stale_memory_never_reports_local_model_admitted(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    _put(tmp_path, "worker-memory-admission.json", {
        "observed_at_utc": (now - timedelta(hours=1)).isoformat(),
        "available_mib": 8192, "status": "NORMAL", "new_worker_budget": 1,
    })
    result = evidence.measured_local_admission(tmp_path, now=now)
    assert result["status"] == "UNVERIFIED_MEMORY"
    assert result["model"] is None
    assert result["new_worker_authorized"] is False


def test_unknown_model_profile_is_not_treated_as_qualified(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    _put(tmp_path, "iron-god-capability-profiles/claude_pro.json", {
        "schema_version": 1, "model": "claude/pro",
        "max_passing_level": 99,
    })
    result = evidence.measured_local_admission(tmp_path, now=now)
    assert result["status"] == "CALIBRATION_UNVERIFIED"
    assert result["model"] is None


def test_publication_projection_reports_pr_without_claiming_install_or_acceptance(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    _put(tmp_path, "iron-god-products/publications.json", {
        "schema_version": 1,
        "items": {
            "packet-123": {
                "repository": REPOSITORY, "state": "MERGED",
                "head_sha": SHA, "merged_sha": "b" * 40,
                "pr": {"number": 123, "url": "https://private.example/secret"},
                "body": "SECRET_PROTECTED_PR_BODY",
            },
        },
    })
    result = evidence.engineering_bundle(tmp_path, repository=REPOSITORY, now=now)
    publications = result["delivery"]["local_publication_receipts"]
    assert publications["status"] == "OBSERVED_LOCAL"
    receipt = publications["receipts"][0]
    assert receipt["pr_number"] == 123
    assert receipt["head_sha"] == SHA
    assert receipt["local_merged_sha"] == "b" * 40
    assert receipt["current_head_ci_verified"] is False
    assert receipt["release_installed_verified"] is False
    assert receipt["product_accepted_verified"] is False
    assert result["delivery"]["native_product_acceptance"] == "UNVERIFIED"
    assert "SECRET_PROTECTED_PR_BODY" not in json.dumps(result)
    assert "private.example" not in json.dumps(result)


def test_publications_missing_or_corrupt_fail_closed_without_creating_files(tmp_path):
    _state(tmp_path, datetime.now(UTC))
    result = evidence.publication_evidence(tmp_path)
    assert result["status"] == "UNAVAILABLE"
    file = tmp_path / "iron-god-products" / "publications.json"
    file.write_text("{bad json", encoding="utf-8")
    result = evidence.publication_evidence(tmp_path)
    assert result["status"] == "UNVERIFIED"
    assert result["receipts"] == []


def _microtask_fixture(root: Path, stamp: datetime, *, repo: str = REPOSITORY):
    from project_orchestrator import iron_god_microtask_progress as progress

    parent = "RM-voice-checkpoint-12345"
    plan = {
        "schema_version": 1, "parent_id": parent, "repository": repo,
        "planned_at_head": SHA, "roadmap_ref": "graph#owner-approved-example",
        "status": "ACTIVE",
        "coverage": {"1": ["t1"], "2": ["t2"]},
        "subtasks": [
            {"key": "t1", "module_path": "pkg/one.py", "depends_on": []},
            {"key": "t2", "module_path": "pkg/two.py", "depends_on": ["t1"]},
        ],
    }
    _put(root, f"iron-god-products/decompositions/{parent}.json", plan)
    path = progress.checkpoint_path(root / "iron-god-products", plan)
    _put(root, str(path.relative_to(root)), {
        "schema_version": 1, "advisory_only": True,
        "product_acceptance": "UNVERIFIED_BY_THIS_CHECKPOINT",
        "parent_id": parent, "repository": repo,
        "planned_at_head": SHA, "observed_repository_head": "b" * 40,
        "plan_status": "ACTIVE", "checkpointed_at_utc": stamp.isoformat(),
        "subtasks": [
            {"key": "t1", "attempt_id": parent + "-t1", "status": "DONE",
             "receipt": "GITHUB_MERGED_IN_LANE_LEDGER", "packet_ids": ["PRODUCT-P1"],
             "attempts": 1, "outcome": "SECRET_private_worker_stack"},
            {"key": "t2", "attempt_id": parent + "-t2-r1", "status": "EXHAUSTED",
             "receipt": "NO_VERIFIED_COMPLETION_RECEIPT",
             "packet_ids": ["PRODUCT-P2"], "attempts": 4,
             "outcome": "SECRET_another_private_worker_log"},
        ],
        "repair_candidates": [{"proposed_action": "SECRET_LOSSY_OWNER_NOTES"}],
    })
    return plan, path


def test_current_plan_microtask_checkpoints_visible_via_mcp_without_replay_authority(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    plan, _ = _microtask_fixture(tmp_path, now)
    data = evidence.engineering_bundle(tmp_path, repository=REPOSITORY, now=now)
    checkpoints = data["durable_continuation"]["local_microtask_progress"]
    assert checkpoints["status"] == "OBSERVED_HISTORICAL"
    assert checkpoints["historical_only"] is True
    assert checkpoints["resume_authorized"] is False
    (row,) = checkpoints["plans"]
    assert row["parent_id"] == plan["parent_id"]
    assert row["plan_sha"] == SHA
    assert row["observed_repository_head"] == "b" * 40
    assert row["freshness"] == "FRESH"
    assert [s["state_as_reported"] for s in row["subtasks"]] == ["DONE", "EXHAUSTED"]
    assert row["subtasks"][0]["receipt_as_reported"] == "GITHUB_MERGED_IN_LANE_LEDGER"
    assert row["subtasks"][0]["verified_independent_git_merge"] is False
    assert row["repair_candidate_count"] == 1
    assert row["replay_or_dispatch_authorized"] is False
    assert "SECRET_" not in json.dumps(data)


def test_checkpoint_survives_restart_as_history_not_live_admission(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now - timedelta(hours=2))
    _microtask_fixture(tmp_path, now - timedelta(hours=2))
    data = evidence.engineering_bundle(tmp_path, repository=REPOSITORY, now=now)
    checkpoint = data["durable_continuation"]["local_microtask_progress"]
    assert checkpoint["plans"][0]["freshness"] == "STALE"
    assert checkpoint["resume_authorized"] is False
    assert data["frontier_and_recovery"]["live_status"] == "UNVERIFIED"


def test_revision_hash_prevents_superseded_checkpoint_becoming_current(tmp_path):
    now = datetime.now(UTC)
    plan, historical_path = _microtask_fixture(tmp_path, now)
    plan["subtasks"][0]["module_path"] = "pkg/changed.py"
    _put(tmp_path, f"iron-god-products/decompositions/{plan['parent_id']}.json", plan)
    data = evidence.microtask_checkpoint_evidence(tmp_path, now=now)
    assert data["plans"] == []
    assert historical_path.is_file()
    assert data["resume_authorized"] is False


def test_microtask_checkpoints_scope_to_registered_repositories_and_limit(tmp_path):
    now = datetime.now(UTC)
    _microtask_fixture(tmp_path, now)
    result = evidence.microtask_checkpoint_evidence(
        tmp_path, repository="jweter/Project-Everward", limit=1, now=now,
    )
    assert result["plans"] == []
    with pytest.raises(ValueError, match="UNKNOWN_OR_UNAUTHORIZED_REPOSITORY"):
        evidence.microtask_checkpoint_evidence(tmp_path, repository="../private")
    with pytest.raises(ValueError, match="INVALID_EVIDENCE_LIMIT"):
        evidence.microtask_checkpoint_evidence(tmp_path, limit=99)


def test_corrupt_microtask_checkpoint_fails_closed_without_overwriting_source(tmp_path):
    now = datetime.now(UTC)
    _, file = _microtask_fixture(tmp_path, now)
    file.write_text("{invalid-json", encoding="utf-8")
    result = evidence.microtask_checkpoint_evidence(tmp_path, now=now)
    assert result["status"] == "UNVERIFIED"
    assert result["plans"] == []
    assert file.read_text(encoding="utf-8") == "{invalid-json"
    assert result["resume_authorized"] is False


def test_corrupt_unrelated_plan_does_not_hide_valid_repository_checkpoint(tmp_path):
    now = datetime.now(UTC)
    good, _ = _microtask_fixture(tmp_path, now)
    (tmp_path / "iron-god-products" / "decompositions" / "other-broken.json").write_text(
        "{invalid-json", encoding="utf-8",
    )
    result = evidence.microtask_checkpoint_evidence(
        tmp_path, repository=REPOSITORY, now=now,
    )
    assert result["status"] == "PARTIAL_UNVERIFIED"
    assert result["corrupt_records"] == 1
    assert len(result["plans"]) == 1
    assert result["plans"][0]["parent_id"] == good["parent_id"]
    assert result["resume_authorized"] is False
    assert result["plans"][0]["replay_or_dispatch_authorized"] is False


def test_corrupt_other_checkpoint_is_isolated_without_losing_canonical_progress(tmp_path):
    from project_orchestrator import iron_god_microtask_progress as progress

    now = datetime.now(UTC)
    valid_plan, _ = _microtask_fixture(tmp_path, now)
    other = {**valid_plan, "parent_id": "RM-other-branch"}
    _put(tmp_path, "iron-god-products/decompositions/RM-other-branch.json", other)
    other_checkpoint = progress.checkpoint_path(tmp_path / "iron-god-products", other)
    other_checkpoint.parent.mkdir(parents=True, exist_ok=True)
    other_checkpoint.write_text("broken-json", encoding="utf-8")

    result = evidence.microtask_checkpoint_evidence(tmp_path, now=now)
    assert result["status"] == "PARTIAL_UNVERIFIED"
    assert result["corrupt_records"] == 1
    assert len(result["plans"]) == 1
    assert result["plans"][0]["parent_id"] == valid_plan["parent_id"]
    assert other_checkpoint.read_text(encoding="utf-8") == "broken-json"


def test_only_corrupt_plans_result_in_unverified_not_false_empty_queue(tmp_path):
    plan_dir = tmp_path / "iron-god-products" / "decompositions"
    plan_dir.mkdir(parents=True)
    (plan_dir / "bad.json").write_text("{", encoding="utf-8")
    result = evidence.microtask_checkpoint_evidence(tmp_path)
    assert result["status"] == "UNVERIFIED"
    assert result["plans"] == []
    assert result["corrupt_records"] == 1
    assert result["resume_authorized"] is False


@pytest.mark.parametrize("malformed", [
    {"subtasks": 1},
    {"subtasks": {"t1": {"status": "DONE"}}},
    {"subtasks": "untrusted"},
    {"subtasks": [None]},
    {"subtasks": []},
    {"repair_candidates": {"fake": "authorized"}},
])
def test_malformed_checkpoint_shape_isolated_without_hiding_valid_sibling_evidence(
    tmp_path, malformed
):
    from project_orchestrator import iron_god_microtask_progress as progress

    now = datetime.now(UTC)
    good, _ = _microtask_fixture(tmp_path, now)
    bad = {**good, "parent_id": "RM-another-repo-shape"}
    # Corrupt sibling in the same repository must remain isolated.
    _put(tmp_path, "iron-god-products/decompositions/RM-another-repo-shape.json", bad)
    path = progress.checkpoint_path(tmp_path / "iron-god-products", bad)
    payload = {
        "schema_version": 1,
        "product_acceptance": "UNVERIFIED_BY_THIS_CHECKPOINT",
        "parent_id": bad["parent_id"],
        "repository": bad["repository"],
        "planned_at_head": SHA,
        "checkpointed_at_utc": now.isoformat(),
        "subtasks": [{"key": "t1", "status": "FAILED", "packet_ids": []}],
        "repair_candidates": [],
    }
    payload.update(malformed)
    _put(tmp_path, str(path.relative_to(tmp_path)), payload)

    result = evidence.microtask_checkpoint_evidence(
        tmp_path, repository=REPOSITORY, now=now,
    )
    assert result["status"] == "PARTIAL_UNVERIFIED"
    assert result["corrupt_records"] == 1
    assert len(result["plans"]) == 1
    assert result["plans"][0]["parent_id"] == good["parent_id"]
    assert result["resume_authorized"] is False
    assert result["plans"][0]["replay_or_dispatch_authorized"] is False


def test_all_malformed_checkpoint_shapes_do_not_claim_empty_approved_work(tmp_path):
    from project_orchestrator import iron_god_microtask_progress as progress

    now = datetime.now(UTC)
    plan, path = _microtask_fixture(tmp_path, now)
    corrupted = json.loads(path.read_text(encoding="utf-8"))
    corrupted["subtasks"] = 1
    _put(tmp_path, str(path.relative_to(tmp_path)), corrupted)
    result = evidence.microtask_checkpoint_evidence(tmp_path, now=now)
    assert result["status"] == "UNVERIFIED"
    assert result["corrupt_records"] == 1
    assert result["plans"] == []
    assert result["resume_authorized"] is False

@pytest.mark.parametrize("source", ["events", "attempts"])
def test_future_schema_is_unverified_without_mutation(tmp_path, source):
    if source == "events":
        now = datetime.now(UTC)
        _state(tmp_path, now)
        assert obs.record_supervisor_pass(tmp_path, now=now)
        path = tmp_path / "observability" / "development-events.sqlite"
        read = evidence.event_history
    else:
        path = _attempt_db(tmp_path)
        read = evidence.attempt_memory
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    before = path.read_bytes()
    assert read(tmp_path)["status"] == "UNVERIFIED"
    assert path.read_bytes() == before


def test_malformed_nested_event_fails_closed(tmp_path):
    now = datetime.now(UTC)
    _state(tmp_path, now)
    assert obs.record_supervisor_pass(tmp_path, now=now)
    path = tmp_path / "observability" / "development-events.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER development_events_no_update")
        db.execute("UPDATE development_events SET evidence_json=?", (json.dumps({"lanes": ["invalid"]}),))
    assert evidence.event_history(tmp_path)["status"] == "UNVERIFIED"
