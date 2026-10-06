from __future__ import annotations

from types import SimpleNamespace

import pytest

from project_orchestrator.builder_promotion import promote_candidate
from project_orchestrator.promotion_queue import (
    PromotionQueueError,
    dispatch_eligible_entries,
    enqueue_and_release_worker,
    enqueue_promotion,
    advance_promotion_entry,
    load_queue,
    mark_merge_enqueued,
    mark_merged,
    record_merge_failure,
    scan_merge_ready_throughput_defects,
    write_queue,
)
from project_orchestrator.throughput_guard import build_active_work_lease

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
T0 = "2026-09-10T20:00:00+00:00"
T1 = "2026-09-10T20:10:00+00:00"
T2 = "2026-09-10T21:00:00+00:00"
REPO = "jweter/knowledge-engine-ai"
BRANCH = "orchestrator/report-fix"


def _enqueue(queue=None, *, head=SHA_B, pr_number=101, now=T0):
    queue = queue if queue is not None else {"schema_version": 1, "entries": {}}
    return enqueue_promotion(
        queue,
        repository=REPO,
        branch=BRANCH,
        base_sha=SHA_A,
        head_sha=head,
        changed_paths=["src/report.py"],
        now=now,
        pr_number=pr_number,
    )


def _live(**overrides):
    value = {
        "repository": REPO,
        "default_branch": "main",
        "default_head_sha": SHA_A,
        "branch": BRANCH,
        "branch_head_sha": SHA_B,
        "ahead_by": 1,
        "behind_by": 0,
        "changed_files": ["src/report.py"],
        "open_pr": 101,
        "actively_owned": False,
        "roadmap_authorized": True,
        "contract_uncertain": False,
        "overlap_detected": False,
    }
    value.update(overrides)
    return value


def _green_evidence(**overrides):
    value = {
        "head_sha": SHA_B,
        "verified_head_sha": SHA_B,
        "ci_status": "completed",
        "ci_conclusion": "success",
        "mergeable": True,
        "required_reviews_satisfied": True,
        "review_state": "APPROVED",
        "unresolved_review_threads": 0,
        "lease_valid": True,
        "policy_clear": True,
    }
    value.update(overrides)
    return value


def test_duplicate_enqueue_is_idempotent():
    queue, first = _enqueue()
    queue, second = _enqueue(queue)
    assert first["status"] == "ENQUEUED"
    assert second["status"] == "ALREADY_QUEUED"
    assert len(queue["entries"]) == 1
    assert second["entry"]["entry_id"] == first["entry"]["entry_id"]


def test_enqueue_keys_on_repository_and_pr_not_raw_head():
    queue, first = _enqueue(head=SHA_B, pr_number=101)
    # A fresh commit pushed to the same PR before any reconciliation poll runs.
    queue, moved = _enqueue(queue, head=SHA_C, pr_number=101)
    assert moved["status"] == "REBOUND_STALE_HEAD"
    assert len(queue["entries"]) == 1
    entry = moved["entry"]
    assert entry["head_sha"] == SHA_C
    assert entry["state"] == "CI_PENDING"


def test_enqueue_without_pr_number_keys_on_exact_head():
    queue, a = _enqueue(head=SHA_B, pr_number=None)
    queue, b = _enqueue(queue, head=SHA_C, pr_number=None)
    assert a["status"] == "ENQUEUED"
    assert b["status"] == "ENQUEUED"
    assert len(queue["entries"]) == 2


def test_restart_persistence_round_trips_exactly(tmp_path):
    queue, _ = _enqueue()
    path = tmp_path / "promotion-queue.json"
    write_queue(path, queue)

    reloaded = load_queue(path)
    assert reloaded == queue

    # Simulate a fresh process reloading after a crash/restart.
    reloaded_again, result = _enqueue(reloaded)
    assert result["status"] == "ALREADY_QUEUED"


def test_restart_with_missing_file_starts_empty(tmp_path):
    queue = load_queue(tmp_path / "does-not-exist.json")
    assert queue == {"schema_version": 1, "entries": {}}


def test_head_movement_detected_during_reconciliation_resets_to_ci_pending():
    queue, enqueued = _enqueue(head=SHA_B)
    entry_id = enqueued["entry"]["entry_id"]

    live = _live(branch_head_sha=SHA_C)
    queue, result = advance_promotion_entry(queue, entry_id, live=live, evidence=None, now=T1)

    assert result["status"] == "CI_PENDING"
    assert result["entry"]["head_sha"] == SHA_C
    assert result["entry"]["reason"] == "LIVE_BRANCH_HEAD_MOVED"


def test_head_movement_detected_during_evidence_evaluation_resets_to_ci_pending():
    queue, enqueued = _enqueue(head=SHA_B)
    entry_id = enqueued["entry"]["entry_id"]

    live = _live()
    evidence = _green_evidence(head_sha=SHA_C, verified_head_sha=None)
    queue, result = advance_promotion_entry(queue, entry_id, live=live, evidence=evidence, now=T1)

    assert result["status"] == "CI_PENDING"
    assert result["entry"]["head_sha"] == SHA_C
    assert result["recovery"]["preflight_reuse_allowed"] is False


def test_ci_failure_routes_to_repair_required_with_backoff():
    queue, enqueued = _enqueue()
    entry_id = enqueued["entry"]["entry_id"]

    live = _live()
    evidence = _green_evidence(
        ci_conclusion="failure",
        log_text="FAILED tests/test_x.py::test_y AssertionError",
    )
    queue, result = advance_promotion_entry(queue, entry_id, live=live, evidence=evidence, now=T1)

    assert result["status"] == "REPAIR_REQUIRED"
    entry = result["entry"]
    assert entry["failure_class"] == "TEST_FAILURE"
    assert entry["attempts"] == 1
    assert entry["retry"]["backoff_seconds"] > 0
    assert not dispatch_eligible_entries(queue, now=T1)
    assert dispatch_eligible_entries(queue, now=entry["retry"]["next_eligible_at"])


def test_policy_failure_blocks_without_weakening_gate():
    queue, enqueued = _enqueue()
    entry_id = enqueued["entry"]["entry_id"]

    live = _live()
    evidence = _green_evidence(policy_clear=False)
    queue, result = advance_promotion_entry(queue, entry_id, live=live, evidence=evidence, now=T1)

    assert result["status"] == "BLOCKED"
    assert result["promotion"]["reason"] == "POLICY_GATE_NOT_CLEAR"


def test_review_not_satisfied_routes_to_review_pending():
    queue, enqueued = _enqueue()
    entry_id = enqueued["entry"]["entry_id"]

    live = _live()
    evidence = _green_evidence(unresolved_review_threads=1)
    queue, result = advance_promotion_entry(queue, entry_id, live=live, evidence=evidence, now=T1)

    assert result["status"] == "REVIEW_PENDING"


def test_green_promotion_reaches_merge_ready_without_merge_authority():
    queue, enqueued = _enqueue()
    entry_id = enqueued["entry"]["entry_id"]

    queue, pending = advance_promotion_entry(queue, entry_id, live=_live(), evidence=None, now=T1)
    assert pending["status"] == "CI_PENDING"

    queue, result = advance_promotion_entry(queue, entry_id, live=_live(), evidence=_green_evidence(), now=T2)
    assert result["status"] == "MERGE_READY"
    assert result["promotion"]["merge_allowed"] is False


def test_merge_ready_requires_merge_ready_state_to_enqueue():
    queue, enqueued = _enqueue()
    entry_id = enqueued["entry"]["entry_id"]
    with pytest.raises(PromotionQueueError):
        mark_merge_enqueued(queue, entry_id, now=T1)


def test_merge_failure_retries_transient_platform_gate_from_merge_ready():
    queue, enqueued = _enqueue()
    entry_id = enqueued["entry"]["entry_id"]
    queue, _ = advance_promotion_entry(queue, entry_id, live=_live(), evidence=_green_evidence(), now=T1)
    queue, dispatched = mark_merge_enqueued(queue, entry_id, now=T1)
    assert dispatched["status"] == "MERGE_ENQUEUED"

    queue, failure = record_merge_failure(
        queue,
        entry_id,
        {"workflow_conclusion": "action_required", "jobs_count": 0},
        now=T2,
    )
    assert failure["status"] == "MERGE_READY"
    entry = failure["entry"]
    assert entry["retry"]["backoff_seconds"] > 0
    assert entry["attempts"] == 1


def test_merge_failure_with_code_failure_routes_to_repair_required():
    queue, enqueued = _enqueue()
    entry_id = enqueued["entry"]["entry_id"]
    queue, _ = advance_promotion_entry(queue, entry_id, live=_live(), evidence=_green_evidence(), now=T1)
    queue, _ = mark_merge_enqueued(queue, entry_id, now=T1)

    queue, failure = record_merge_failure(
        queue,
        entry_id,
        {"log_text": "SyntaxError: invalid syntax"},
        now=T2,
    )
    assert failure["status"] == "REPAIR_REQUIRED"
    assert failure["entry"]["failure_class"] == "CODE_FAILURE"


def test_merge_failure_requires_merge_enqueued_state():
    queue, enqueued = _enqueue()
    entry_id = enqueued["entry"]["entry_id"]
    with pytest.raises(PromotionQueueError):
        record_merge_failure(queue, entry_id, {"log_text": "x"}, now=T1)


def test_merge_success_is_terminal_and_idempotent_advance_is_a_no_op():
    queue, enqueued = _enqueue()
    entry_id = enqueued["entry"]["entry_id"]
    queue, _ = advance_promotion_entry(queue, entry_id, live=_live(), evidence=_green_evidence(), now=T1)
    queue, _ = mark_merge_enqueued(queue, entry_id, now=T1)
    queue, merged = mark_merged(queue, entry_id, now=T2, merge_commit_sha=SHA_C)

    assert merged["status"] == "MERGED"
    assert merged["entry"]["merge_commit_sha"] == SHA_C

    queue, no_op = advance_promotion_entry(queue, entry_id, live=_live(), evidence=_green_evidence(), now=T2)
    assert no_op["status"] == "NO_OP_TERMINAL"
    assert no_op["entry"]["state"] == "MERGED"


def test_worker_capacity_is_released_immediately_on_enqueue():
    lease = build_active_work_lease(
        repository=REPO,
        branch=BRANCH,
        head_sha=SHA_B,
        actor="specialist-knowledge-engine",
        state="IMPLEMENTING",
        acquired_at=T0,
    )
    result = enqueue_and_release_worker(
        {"schema_version": 1, "entries": {}},
        repository=REPO,
        branch=BRANCH,
        base_sha=SHA_A,
        head_sha=SHA_B,
        changed_paths=["src/report.py"],
        now=T0,
        pr_number=101,
        active_lease=lease,
    )
    assert result["worker_capacity_released"] is True
    assert result["released_lease"]["state"] == "RELEASED"
    assert result["enqueue"]["status"] == "ENQUEUED"
    assert result["queue"]["entries"][result["enqueue"]["entry"]["entry_id"]]["state"] == "CI_PENDING"


def test_promotion_failure_never_reacquires_worker_capacity():
    """A promotion failure must never give code a path back to occupied capacity.

    enqueue_and_release_worker is the only function in this module that touches a
    lease. Every later transition (CI failure, merge failure) operates purely on
    the queue entry and returns no lease object at all, so there is no code path
    by which a promotion-queue failure could re-occupy development capacity.
    """
    queue, enqueued = _enqueue()
    entry_id = enqueued["entry"]["entry_id"]

    queue, ci_failure = advance_promotion_entry(
        queue,
        entry_id,
        live=_live(),
        evidence=_green_evidence(ci_conclusion="failure", log_text="SyntaxError: invalid syntax"),
        now=T1,
    )
    assert "released_lease" not in ci_failure
    assert "active_lease" not in ci_failure

    queue, _ = advance_promotion_entry(queue, entry_id, live=_live(), evidence=_green_evidence(), now=T2)
    queue, _ = mark_merge_enqueued(queue, entry_id, now=T2)
    queue, merge_failure = record_merge_failure(queue, entry_id, {"log_text": "SyntaxError: invalid syntax"}, now=T2)
    assert "released_lease" not in merge_failure
    assert "active_lease" not in merge_failure


def test_unprocessed_green_merge_ready_is_a_p1_throughput_defect():
    queue, enqueued = _enqueue()
    entry_id = enqueued["entry"]["entry_id"]
    queue, result = advance_promotion_entry(queue, entry_id, live=_live(), evidence=_green_evidence(), now=T1)
    assert result["status"] == "MERGE_READY"

    fresh = scan_merge_ready_throughput_defects(queue, now=T1, stale_after_seconds=900)
    assert fresh == []

    later = "2026-09-10T20:25:01+00:00"
    defects = scan_merge_ready_throughput_defects(queue, now=later, stale_after_seconds=900)
    assert len(defects) == 1
    assert defects[0]["priority"] == "P1"
    assert defects[0]["defect"] == "UNPROCESSED_GREEN_MERGE_READY"
    assert defects[0]["entry_id"] == entry_id


def test_builder_hands_published_candidate_to_durable_queue_and_releases_worker(tmp_path):
    """builder_promotion.promote_candidate's worker is done the instant a PR opens;
    the durable queue (not this worker) owns everything from here on."""
    ws = tmp_path / "ws"
    ws.mkdir()
    queue_holder = {"queue": {"schema_version": 1, "entries": {}}}
    captured = []

    def preflight(_ws, head):
        return SimpleNamespace(returncode=0, stdout="", stderr=""), head, True

    def run(argv, _ws):
        if argv[:3] == ["git", "push", "-u"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="https://github.com/jweter/x/pull/9", stderr="")

    def promotion_queue_fn(**kwargs):
        captured.append(kwargs)
        result = enqueue_and_release_worker(
            queue_holder["queue"],
            now=T0,
            **kwargs,
        )
        queue_holder["queue"] = result["queue"]

    result = promote_candidate(
        ws=ws,
        state_path=tmp_path / "state.json",
        repo="jweter/x",
        idx=0,
        main_branch="main",
        branch="b",
        base=SHA_A,
        head=SHA_B,
        paths=["x.py"],
        slice_authority=None,
        execution_grant=None,
        run_exact_head_preflight_fn=preflight,
        reset_workspace_fn=lambda *_: None,
        retain_failure_lesson_fn=lambda *_: {"fingerprint": "preflight-fp"},
        bind_preflight_evidence_fn=lambda *_: {},
        review_candidate_fn=lambda *_: None,
        advance_grant_stage_fn=lambda *_a, **_k: None,
        refresh_base_before_publish_fn=lambda *_: {"status": "UNCHANGED", "base_sha": SHA_A, "head_sha": SHA_B},
        changed_paths_fn=lambda *_: ["x.py"],
        generated_change_failure_fn=lambda *_: None,
        run_fn=run,
        pr_number_from_url_fn=lambda _url: 9,
        promotion_queue_fn=promotion_queue_fn,
    )

    assert result["status"] == "PR_OPEN"
    assert len(captured) == 1
    assert captured[0]["head_sha"] == SHA_B
    assert captured[0]["pr_number"] == 9

    (entry,) = queue_holder["queue"]["entries"].values()
    assert entry["state"] == "CI_PENDING"
    assert entry["repository"] == "jweter/x"


def test_builder_does_not_enqueue_on_preflight_failure(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    captured = []

    def preflight(_ws, head):
        return SimpleNamespace(returncode=1, stdout="", stderr="bad"), head, True

    result = promote_candidate(
        ws=ws,
        state_path=tmp_path / "state.json",
        repo="jweter/x",
        idx=0,
        main_branch="main",
        branch="b",
        base=SHA_A,
        head=SHA_B,
        paths=["x.py"],
        slice_authority=None,
        execution_grant=None,
        run_exact_head_preflight_fn=preflight,
        reset_workspace_fn=lambda *_: None,
        retain_failure_lesson_fn=lambda *_: {"fingerprint": "preflight-fp"},
        bind_preflight_evidence_fn=lambda *_: {},
        review_candidate_fn=lambda *_: None,
        advance_grant_stage_fn=lambda *_a, **_k: None,
        refresh_base_before_publish_fn=lambda *_: {"status": "UNCHANGED", "base_sha": SHA_A, "head_sha": SHA_B},
        changed_paths_fn=lambda *_: ["x.py"],
        generated_change_failure_fn=lambda *_: None,
        run_fn=lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="", stderr=""),
        pr_number_from_url_fn=lambda _url: 9,
        promotion_queue_fn=lambda **kwargs: captured.append(kwargs),
    )

    assert result["status"] == "PREFLIGHT_FAILED"
    assert captured == []
