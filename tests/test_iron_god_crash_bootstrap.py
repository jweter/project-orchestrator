from __future__ import annotations

from pathlib import Path

from project_orchestrator.iron_god_crash_bootstrap import (
    ANCHOR_AND_REVIEW,
    KNOWN,
    NO_ACTION,
    ORPHANED,
    bootstrap_classify,
)
from project_orchestrator.local_orphan_worktree_scan import (
    WorktreeClassification,
    WorktreeEntry,
)


def _classification(
    name: str,
    *,
    missing: bool = False,
    dirty: bool = False,
    ambiguous: bool = False,
) -> WorktreeClassification:
    entry = WorktreeEntry(
        path=Path("/repo") / name,
        head="a" * 40,
        branch=f"refs/heads/{name}",
        bare=False,
        detached=False,
        locked=False,
        lock_reason=None,
        prunable=False,
        prunable_reason=None,
    )
    return WorktreeClassification(
        entry=entry,
        missing=missing,
        dirty=dirty,
        ambiguous=ambiguous,
        ignored_untracked_paths=(),
        assume_unchanged_paths=(),
        skip_worktree_paths=(),
        notes=(),
    )


def test_empty_input_yields_empty_result():
    assert bootstrap_classify((), frozenset()) == ()


def test_known_worktree_gets_no_action():
    classification = _classification("ig050-worker-1")
    result = bootstrap_classify((classification,), frozenset({"ig050-worker-1"}))

    assert len(result) == 1
    entry = result[0]
    assert entry.worktree_name == "ig050-worker-1"
    assert entry.classification == KNOWN
    assert entry.recovery_action == NO_ACTION


def test_unregistered_worktree_is_orphaned_and_anchored_never_deleted():
    classification = _classification("stray-worker-9")
    result = bootstrap_classify((classification,), frozenset({"ig050-worker-1"}))

    assert len(result) == 1
    entry = result[0]
    assert entry.worktree_name == "stray-worker-9"
    assert entry.classification == ORPHANED
    assert entry.recovery_action == ANCHOR_AND_REVIEW
    assert "delete" not in entry.recovery_action.lower()


def test_orphan_action_is_anchor_regardless_of_dirty_or_ambiguous_state():
    clean = _classification("stray-clean")
    dirty = _classification("stray-dirty", dirty=True)
    ambiguous = _classification("stray-ambiguous", ambiguous=True)
    missing = _classification("stray-missing", missing=True)

    result = bootstrap_classify(
        (clean, dirty, ambiguous, missing), known_worktree_names=frozenset()
    )

    assert all(entry.classification == ORPHANED for entry in result)
    assert all(entry.recovery_action == ANCHOR_AND_REVIEW for entry in result)


def test_mixed_known_and_orphaned_worktrees_classified_independently():
    known = _classification("ig050-worker-1")
    orphan = _classification("unregistered-worker")

    result = bootstrap_classify(
        (known, orphan), known_worktree_names=frozenset({"ig050-worker-1"})
    )

    by_name = {entry.worktree_name: entry for entry in result}
    assert by_name["ig050-worker-1"].classification == KNOWN
    assert by_name["unregistered-worker"].classification == ORPHANED
