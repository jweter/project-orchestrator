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
