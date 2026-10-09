"""Regression coverage for orphan-safe GitHub evidence polling."""
from pathlib import Path
import subprocess
import sys

import pytest

import scripts.github_evidence_watch_loop as watcher


def test_singleton_rejects_duplicate_without_displacing_live_owner(tmp_path):
    first = watcher.acquire_singleton(tmp_path)
    assert first is not None
    try:
        assert watcher.acquire_singleton(tmp_path) is None
        assert (tmp_path / "github-evidence-watcher.lock").exists()
    finally:
        watcher.release_singleton(first)
    second = watcher.acquire_singleton(tmp_path)
    assert second is not None
    watcher.release_singleton(second)


def test_duplicate_run_never_collects_or_writes_snapshot(tmp_path, monkeypatch):
    first = watcher.acquire_singleton(tmp_path)
    assert first is not None
    calls = []
    monkeypatch.setattr(watcher, "collect_once", lambda *_: calls.append(True))
    try:
        assert watcher.run(tmp_path, tmp_path, poll_seconds=60, once=True) == 0
        assert calls == []
        assert not (tmp_path / "github-evidence-watcher.json").exists()
    finally:
        watcher.release_singleton(first)


def test_once_releases_lock_and_next_launch_can_collect(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(watcher, "collect_once", lambda *_: calls.append(True))
    for _ in range(2):
        assert watcher.run(tmp_path, tmp_path, poll_seconds=60, once=True) == 0
    assert calls == [True, True]


def test_exception_releases_lock_for_recovery(tmp_path, monkeypatch):
    def fail(*_):
        raise RuntimeError("simulated collection crash")

    monkeypatch.setattr(watcher, "collect_once", fail)
    with pytest.raises(RuntimeError, match="simulated collection crash"):
        watcher.run(tmp_path, tmp_path, poll_seconds=60, once=True)
    acquired = watcher.acquire_singleton(tmp_path)
    assert acquired is not None
    watcher.release_singleton(acquired)


def test_kernel_lock_rejects_other_process_then_recovers_after_exit(tmp_path):
    code = (
        "from pathlib import Path;"
        "from scripts.github_evidence_watch_loop import acquire_singleton;"
        f"assert acquire_singleton(Path({str(tmp_path)!r})) is None"
    )
    root = Path(watcher.__file__).resolve().parents[1]
    owner = watcher.acquire_singleton(tmp_path)
    assert owner is not None
    try:
        blocked = subprocess.run(
            [sys.executable, "-c", code], cwd=root,
            capture_output=True, text=True, check=False,
        )
        assert blocked.returncode == 0, blocked.stderr
    finally:
        watcher.release_singleton(owner)
    recovered = subprocess.run(
        [sys.executable, "-c", code.replace("is None", "is not None")],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert recovered.returncode == 0, recovered.stderr
