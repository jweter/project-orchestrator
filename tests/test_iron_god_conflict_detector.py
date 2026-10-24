from __future__ import annotations

import pytest

from project_orchestrator.iron_god_conflict_detector import (
    conflict_free_subset,
    detect_conflict,
    footprint,
)


def test_footprint_rejects_empty_slice_id():
    with pytest.raises(ValueError):
        footprint("", ["a.py"])


def test_footprint_rejects_empty_changed_paths():
    with pytest.raises(ValueError):
        footprint("IG-100", [])


def test_detect_conflict_none_when_disjoint():
    a = footprint("IG-100", ["src/a.py", "tests/test_a.py"])
    b = footprint("IG-101", ["src/b.py", "tests/test_b.py"])
    report = detect_conflict(a, b)
    assert report.conflicts is False
    assert report.conflicting_paths == frozenset()


def test_detect_conflict_reports_exact_overlap():
    a = footprint("IG-100", ["src/shared.py", "src/a.py"])
    b = footprint("IG-101", ["src/shared.py", "src/b.py"])
    report = detect_conflict(a, b)
    assert report.conflicts is True
    assert report.conflicting_paths == frozenset({"src/shared.py"})


def test_detect_conflict_rejects_comparing_slice_to_itself():
    a = footprint("IG-100", ["src/a.py"])
    a_again = footprint("IG-100", ["src/a.py"])
    with pytest.raises(ValueError):
        detect_conflict(a, a_again)


def test_conflict_free_subset_all_independent():
    a = footprint("IG-100", ["src/a.py"])
    b = footprint("IG-101", ["src/b.py"])
    c = footprint("IG-102", ["src/c.py"])
    selected, reports = conflict_free_subset([a, b, c])
    assert selected == [a, b, c]
    assert reports == []


def test_conflict_free_subset_excludes_later_conflicting_slice():
    a = footprint("IG-100", ["src/shared.py"])
    b = footprint("IG-101", ["src/shared.py"])  # conflicts with a
    c = footprint("IG-102", ["src/c.py"])  # independent of both
    selected, reports = conflict_free_subset([a, b, c])
    assert selected == [a, c]
    assert len(reports) == 1
    assert reports[0].slice_a == "IG-101"
    assert reports[0].slice_b == "IG-100"


def test_conflict_free_subset_rejects_duplicate_slice_id():
    a = footprint("IG-100", ["src/a.py"])
    a_dup = footprint("IG-100", ["src/other.py"])
    with pytest.raises(ValueError):
        conflict_free_subset([a, a_dup])
