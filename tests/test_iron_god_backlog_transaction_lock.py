"""Cross-process transaction safety for the shared Iron God backlog state."""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
import threading
import time

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator.grant_lifecycle import (
    acquire_lifecycle_lock,
    release_lifecycle_lock,
)


def test_item_and_block_updates_preserve_both_items(tmp_path, monkeypatch):
    """The two write APIs must serialize their entire read/modify/write transactions."""
    original_load = backlog.load_state
    overlap = threading.Barrier(2)

    def load_with_forced_overlap(root):
        state = original_load(root)
        try:
            overlap.wait(timeout=0.25)
        except threading.BrokenBarrierError:
            pass  # Correct locking keeps the second writer outside the read phase.
        return state

    monkeypatch.setattr(backlog, "load_state", load_with_forced_overlap)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(
            backlog.record_item, tmp_path, "A", packet_id="a-1", outcome="FAIL"
        )
        b = pool.submit(backlog.record_block, tmp_path, "B", outcome="BLOCKED_CONFLICT")
        a.result(timeout=8)
        b.result(timeout=8)

    items = original_load(tmp_path)["items"]
    assert set(items) == {"A", "B"}
    assert items["A"]["attempts"] == 1
    assert items["B"]["attempts"] == 0
    assert items["B"]["outcome"] == "BLOCKED_CONFLICT"


def test_block_write_respects_cross_process_lock(tmp_path):
    """A second process cannot commit a block outcome while the state lock is held."""
    ready = tmp_path / "ready"
    child_script = (
        "import sys; from pathlib import Path; "
        "from project_orchestrator.iron_god_backlog import record_block; "
        "Path(sys.argv[2]).write_text('ready'); "
        "record_block(Path(sys.argv[1]), 'child', outcome='BLOCKED_CONFLICT')"
    )
    lock = acquire_lifecycle_lock(backlog.state_path(tmp_path))
    child = None
    try:
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", child_script, str(tmp_path), str(ready)],
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 8
        while (
            not ready.exists() and child.poll() is None and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert ready.exists(), "child did not reach lock boundary"
        time.sleep(0.15)
        assert not backlog.state_path(tmp_path).exists(), (
            "block writer bypassed transaction lock"
        )
    finally:
        release_lifecycle_lock(lock)
    try:
        stdout, stderr = child.communicate(timeout=8)
        assert child.returncode == 0, (stdout, stderr)
        assert (
            backlog.load_state(tmp_path)["items"]["child"]["outcome"]
            == "BLOCKED_CONFLICT"
        )
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=5)
