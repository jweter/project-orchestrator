from project_orchestrator.iron_god_failed_source import (
    source_fingerprint,
    repeated_failed_source,
)


def test_exact_repeat_detected():
    failed = {"cache.py": b"size += removed"}
    assert repeated_failed_source(failed, {source_fingerprint(failed)})


def test_different_fix_allowed():
    old = {"cache.py": b"size += removed"}
    new = {"cache.py": b"size -= removed"}
    assert not repeated_failed_source(new, {source_fingerprint(old)})


def test_order_independent():
    assert source_fingerprint({"a.py": b"a", "b.py": b"b"}) == source_fingerprint(
        {"b.py": b"b", "a.py": b"a"}
    )


def test_path_included():
    assert source_fingerprint({"a.py": b"a"}) != source_fingerprint({"b.py": b"a"})


def test_path_traversal_rejected():
    import pytest

    with pytest.raises(ValueError):
        source_fingerprint({"../outside.py": b"x"})


def test_backlog_records_duplicate_failed_source(tmp_path):
    from project_orchestrator.iron_god_backlog import record_item, load_state

    digest = source_fingerprint({"cache.py": b"broken"})
    first = record_item(
        tmp_path,
        "KEC-2",
        packet_id="attempt-1",
        outcome="FAIL",
        failed_source_fingerprint=digest,
    )
    assert first["repeated_failed_source"] is False
    second = record_item(
        tmp_path,
        "KEC-2",
        packet_id="attempt-2",
        outcome="FAIL",
        failed_source_fingerprint=digest,
    )
    assert second["repeated_failed_source"] is True
    assert second["attempts"] == 2
    assert load_state(tmp_path)["items"]["KEC-2"]["failed_source_fingerprints"] == [
        digest
    ]


def test_backlog_does_not_count_success_as_failed_source(tmp_path):
    from project_orchestrator.iron_god_backlog import record_item

    digest = source_fingerprint({"cache.py": b"correct"})
    row = record_item(
        tmp_path,
        "KEC-3",
        packet_id="attempt-1",
        outcome="ACCEPTED_LOCAL",
        failed_source_fingerprint=digest,
    )
    assert "failed_source_fingerprints" not in row


def test_replayed_same_packet_does_not_become_duplicate(tmp_path):
    from project_orchestrator.iron_god_backlog import record_item

    digest = source_fingerprint({"cache.py": b"broken"})
    first = record_item(
        tmp_path,
        "KEC-2",
        packet_id="attempt-1",
        outcome="FAIL",
        failed_source_fingerprint=digest,
    )
    replay = record_item(
        tmp_path,
        "KEC-2",
        packet_id="attempt-1",
        outcome="FAIL",
        failed_source_fingerprint=digest,
    )
    assert first["repeated_failed_source"] is False
    assert replay["repeated_failed_source"] is False
    assert replay["attempts"] == 1
    assert replay["failed_source_by_packet"] == {"attempt-1": digest}


def test_duplicate_remains_detected_after_first_packet_replay(tmp_path):
    from project_orchestrator.iron_god_backlog import record_item

    digest = source_fingerprint({"cache.py": b"broken"})
    for packet_id in ("attempt-1", "attempt-2", "attempt-1"):
        result = record_item(
            tmp_path,
            "KEC-2",
            packet_id=packet_id,
            outcome="FAIL",
            failed_source_fingerprint=digest,
        )
    assert result["repeated_failed_source"] is True
    assert result["attempts"] == 2


def test_conflicting_replay_preserves_original_evidence(tmp_path):
    from project_orchestrator.iron_god_backlog import record_item

    first = source_fingerprint({"cache.py": b"broken"})
    second = source_fingerprint({"cache.py": b"different"})
    record_item(
        tmp_path,
        "KEC-2",
        packet_id="attempt-1",
        outcome="FAIL",
        failed_source_fingerprint=first,
    )
    result = record_item(
        tmp_path,
        "KEC-2",
        packet_id="attempt-1",
        outcome="FAIL",
        failed_source_fingerprint=second,
    )
    assert result["failed_source_conflict"] is True
    assert result["failed_source_by_packet"]["attempt-1"] == first
    assert result["attempts"] == 1


import pytest
from project_orchestrator.iron_god_failed_source import (
    read_candidate_sources,
    validated_source_path,
)


@pytest.mark.parametrize(
    "bad_path",
    [
        "C:/outside.txt",
        "C:relative.txt",
        r"C:\outside.txt",
        r"\\server\share\outside.txt",
        "/absolute.txt",
        r"\rooted.txt",
        "../outside.txt",
        "nested/../../outside.txt",
        "file.py:stream",
        "",
    ],
)
def test_unsafe_paths_rejected_before_read(bad_path, tmp_path):
    with pytest.raises(ValueError):
        validated_source_path(bad_path)
    with pytest.raises(ValueError):
        read_candidate_sources(tmp_path, [bad_path])


def test_read_candidate_sources_within_worktree(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "module.py").write_bytes(b"working")
    assert read_candidate_sources(tmp_path, ["pkg/module.py"]) == {
        "pkg/module.py": b"working"
    }


def test_read_candidate_sources_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside-source.txt")
    outside.write_bytes(b"outside")
    link = tmp_path / "linked.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Windows symlink privilege unavailable")
    with pytest.raises(ValueError):
        read_candidate_sources(tmp_path, ["linked.txt"])


def test_environment_failure_is_not_recorded_as_failed_source(tmp_path):
    from project_orchestrator.iron_god_backlog import record_item

    digest = source_fingerprint({"cache.py": b"unchanged"})
    row = record_item(
        tmp_path,
        "KEC-2",
        packet_id="environment-1",
        outcome="ENVIRONMENT_FAILURE: TIMEOUT",
        failed_source_fingerprint=digest,
    )
    assert "failed_source_by_packet" not in row
    assert row["attempts"] == 1


def test_repeated_failed_source_blocks_next_dispatch_without_resetting_budget(tmp_path):
    from project_orchestrator.iron_god_backlog import load_state, next_item, record_item

    item = {
        "id": "KEC-2",
        "repository": "jweter/knowledge-engine-core",
        "status": "approved",
        "level": 1,
        "depends_on": [],
    }
    digest = source_fingerprint({"cache.py": b"broken"})
    record_item(
        tmp_path,
        "KEC-2",
        packet_id="attempt-1",
        outcome="FAIL",
        failed_source_fingerprint=digest,
    )
    record_item(
        tmp_path,
        "KEC-2",
        packet_id="attempt-2",
        outcome="FAIL",
        failed_source_fingerprint=digest,
    )
    state = load_state(tmp_path)
    assert state["items"]["KEC-2"]["attempts"] == 2
    assert next_item([item], state, "jweter/knowledge-engine-core", max_level=1) is None


def test_distinct_failed_sources_do_not_preempt_existing_attempt_budget(tmp_path):
    from project_orchestrator.iron_god_backlog import load_state, next_item, record_item

    item = {
        "id": "KEC-2",
        "repository": "jweter/knowledge-engine-core",
        "status": "approved",
        "level": 1,
        "depends_on": [],
    }
    for attempt, content in (("attempt-1", b"broken-v1"), ("attempt-2", b"broken-v2")):
        record_item(
            tmp_path,
            "KEC-2",
            packet_id=attempt,
            outcome="FAIL",
            failed_source_fingerprint=source_fingerprint({"cache.py": content}),
        )
    state = load_state(tmp_path)
    assert state["items"]["KEC-2"]["attempts"] == 2
    assert next_item([item], state, "jweter/knowledge-engine-core", max_level=1) is item


def test_legacy_repeated_source_flag_does_not_veto_single_packet(tmp_path):
    from project_orchestrator.iron_god_backlog import load_state, next_item, record_item

    item = {
        "id": "KEC-2",
        "repository": "jweter/knowledge-engine-core",
        "status": "approved",
        "level": 1,
        "depends_on": [],
    }
    record_item(
        tmp_path,
        "KEC-2",
        packet_id="attempt-1",
        outcome="FAIL",
        failed_source_fingerprint=source_fingerprint({"cache.py": b"broken"}),
    )
    state = load_state(tmp_path)
    state["items"]["KEC-2"]["repeated_failed_source"] = True

    assert next_item([item], state, "jweter/knowledge-engine-core", max_level=1) is item
    assert state["items"]["KEC-2"]["attempts"] == 1
    assert state["items"]["KEC-2"]["packets"] == ["attempt-1"]


def test_packet_fingerprints_veto_even_if_legacy_flag_is_false(tmp_path):
    from project_orchestrator.iron_god_backlog import load_state, next_item, record_item

    item = {
        "id": "KEC-2",
        "repository": "jweter/knowledge-engine-core",
        "status": "approved",
        "level": 1,
        "depends_on": [],
    }
    digest = source_fingerprint({"cache.py": b"broken"})
    for packet_id in ("attempt-1", "attempt-2"):
        record_item(
            tmp_path,
            "KEC-2",
            packet_id=packet_id,
            outcome="FAIL",
            failed_source_fingerprint=digest,
        )
    state = load_state(tmp_path)
    state["items"]["KEC-2"]["repeated_failed_source"] = False

    assert next_item([item], state, "jweter/knowledge-engine-core", max_level=1) is None
    assert state["items"]["KEC-2"]["attempts"] == 2
    assert state["items"]["KEC-2"]["packets"] == ["attempt-1", "attempt-2"]


def test_merged_item_rejects_conflicting_same_packet_replay(tmp_path):
    from project_orchestrator.iron_god_backlog import load_state, next_item, record_item

    item = {
        "id": "KEC-2",
        "repository": "jweter/knowledge-engine-core",
        "status": "approved",
        "level": 1,
        "depends_on": [],
    }
    original = record_item(tmp_path, item["id"], packet_id="packet-1", outcome="MERGED")
    replay = record_item(
        tmp_path,
        item["id"],
        packet_id="packet-1",
        outcome="FAIL",
        failed_source_fingerprint=source_fingerprint({"cache.py": b"bad"}),
    )
    assert replay["outcome"] == "MERGED"
    assert replay["merged_at_utc"] == original["merged_at_utc"]
    assert replay["attempts"] == 1
    assert replay["packets"] == ["packet-1"]
    assert replay["terminal_outcome_conflict"] is True
    assert "failed_source_by_packet" not in replay
    assert next_item([item], load_state(tmp_path), item["repository"], max_level=1) is None


def test_merged_item_rejects_late_new_packet_without_retry_budget_change(tmp_path):
    from project_orchestrator.iron_god_backlog import load_state, record_item

    record_item(tmp_path, "KEC-2", packet_id="packet-1", outcome="MERGED")
    replay = record_item(
        tmp_path,
        "KEC-2",
        packet_id="packet-2",
        outcome="FAIL",
        failed_source_fingerprint=source_fingerprint({"cache.py": b"bad"}),
    )
    assert replay["outcome"] == "MERGED"
    assert replay["attempts"] == 1
    assert replay["packets"] == ["packet-1"]
    assert replay["terminal_outcome_conflict"] is True
    assert load_state(tmp_path)["items"]["KEC-2"]["outcome"] == "MERGED"


def test_dispatched_to_merged_transition_and_merged_replay_are_idempotent(tmp_path):
    from project_orchestrator.iron_god_backlog import record_item

    record_item(tmp_path, "KEC-2", packet_id="packet-1", outcome="DISPATCHED")
    merged = record_item(tmp_path, "KEC-2", packet_id="packet-1", outcome="MERGED")
    replay = record_item(tmp_path, "KEC-2", packet_id="packet-1", outcome="MERGED")
    assert replay == merged
    assert replay["attempts"] == 1
    assert replay["outcome"] == "MERGED"
    assert "terminal_outcome_conflict" not in replay


def test_concurrent_backlog_updates_preserve_both_items(tmp_path, monkeypatch):
    """Two distinct worker outcomes cannot overwrite one another."""
    import concurrent.futures
    import threading

    from project_orchestrator import iron_god_backlog as backlog

    original_load = backlog.load_state
    simultaneous_reads = threading.Barrier(2)

    def load_with_forced_overlap(root):
        state = original_load(root)
        try:
            simultaneous_reads.wait(timeout=0.2)
        except threading.BrokenBarrierError:
            pass  # Serialized writers cannot both enter the read phase.
        return state

    monkeypatch.setattr(backlog, "load_state", load_with_forced_overlap)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(backlog.record_item, tmp_path, "A", packet_id="a-1", outcome="FAIL")
        b = pool.submit(backlog.record_item, tmp_path, "B", packet_id="b-1", outcome="MERGED")
        a.result(timeout=5)
        b.result(timeout=5)

    items = original_load(tmp_path)["items"]
    assert set(items) == {"A", "B"}
    assert items["A"]["attempts"] == 1
    assert items["B"]["outcome"] == "MERGED"


def test_backlog_transaction_lock_blocks_another_process(tmp_path):
    """The read/modify/write lock is effective across processes, not just threads."""
    import os
    import subprocess
    import sys
    import time

    from project_orchestrator.iron_god_backlog import load_state, state_path
    from project_orchestrator.grant_lifecycle import acquire_lifecycle_lock, release_lifecycle_lock

    ready = tmp_path / "child-ready"
    script = (
        "import sys; from pathlib import Path; "
        "from project_orchestrator.iron_god_backlog import record_item; "
        "Path(sys.argv[2]).write_text('ready'); "
        "record_item(Path(sys.argv[1]), 'child', packet_id='child-1', outcome='MERGED')"
    )
    lock = acquire_lifecycle_lock(state_path(tmp_path))
    child = None
    try:
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(tmp_path), str(ready)],
            env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 8
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "child did not reach the transaction boundary"
        time.sleep(0.15)
        assert not state_path(tmp_path).exists(), "child bypassed the held transaction lock"
    finally:
        release_lifecycle_lock(lock)
    try:
        stdout, stderr = child.communicate(timeout=8)
        assert child.returncode == 0, (stdout, stderr)
        assert load_state(tmp_path)["items"]["child"]["outcome"] == "MERGED"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
