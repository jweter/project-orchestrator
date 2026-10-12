"""Refresh authoritative GitHub portfolio evidence outside the supervisor critical loop."""
from __future__ import annotations

import argparse
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from project_orchestrator.github_evidence import UrllibGitHubTransport, collect_portfolio
from project_orchestrator.state_io import atomic_json


def acquire_singleton(state_root: Path) -> BinaryIO | None:
    """Hold a kernel-released lock for the entire evidence-watcher lifetime.

    The supervisor can crash or be killed without reaping its child. Repeated
    launches must not multiply GitHub polling processes.
    """
    state_root.mkdir(parents=True, exist_ok=True)
    handle = (state_root / "github-evidence-watcher.lock").open("a+b", buffering=0)
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ImportError):
        handle.close()
        return None
    return handle


def release_singleton(handle: BinaryIO) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def collect_once(repo_root: Path, state_root: Path) -> dict:
    started = time.monotonic()
    try:
        snapshot = collect_portfolio(
            str(repo_root / "engineering" / "portfolio.json"),
            UrllibGitHubTransport.from_environment(),
        )
        atomic_json(state_root / "github-portfolio-snapshot.json", snapshot)
        result = {
            "status": "SNAPSHOT_FRESH",
            "collected_at_utc": snapshot["collected_at"],
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except Exception as exc:
        result = {
            "status": "SNAPSHOT_REFRESH_FAILED",
            "error": f"{type(exc).__name__}: {exc}",
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    atomic_json(state_root / "github-evidence-watcher.json", result)
    return result


def run(repo_root: Path, state_root: Path, *, poll_seconds: int, once: bool) -> int:
    singleton = acquire_singleton(state_root)
    if singleton is None:
        return 0  # Another live owner is collecting; never launch a duplicate.
    try:
        while True:
            collect_once(repo_root, state_root)
            if once:
                return 0
            time.sleep(max(60, poll_seconds))
    finally:
        release_singleton(singleton)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--poll-seconds", type=int, default=600)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    return run(Path(args.repo_root), Path(args.state_root),
               poll_seconds=args.poll_seconds, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
