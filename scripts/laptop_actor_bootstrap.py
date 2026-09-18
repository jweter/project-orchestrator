from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)


def windows_idle_seconds() -> float | None:
    """Return interactive Windows idle time, or None off Windows."""
    if os.name != "nt":
        return None

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        raise RuntimeError("GetLastInputInfo failed")
    tick = ctypes.windll.kernel32.GetTickCount64()
    return max(0.0, (tick - info.dwTime) / 1000.0)


def update_actor_checkout(actor_repo: Path) -> str:
    """Fast-forward the disposable actor checkout to exact origin/main.

    The checkout is owned by the laptop actor, never by the user's development
    workspace, so discarding actor-checkout drift is safe and intentional.
    """
    if not (actor_repo / ".git").is_dir():
        raise RuntimeError("actor repository is not a Git checkout")
    fetch = run(["git", "fetch", "origin", "main"], actor_repo)
    if fetch.returncode != 0:
        raise RuntimeError("actor checkout fetch failed")
    remote = run(["git", "rev-parse", "origin/main"], actor_repo)
    if remote.returncode != 0:
        raise RuntimeError("origin/main is unavailable")
    target = remote.stdout.strip()
    if len(target) != 40 or any(c not in "0123456789abcdef" for c in target):
        raise RuntimeError("origin/main did not resolve to an exact SHA")
    reset = run(["git", "reset", "--hard", target], actor_repo)
    if reset.returncode != 0:
        raise RuntimeError("actor checkout reset failed")
    clean = run(["git", "status", "--porcelain"], actor_repo)
    if clean.returncode != 0 or clean.stdout.strip():
        raise RuntimeError("actor checkout is not clean after update")
    return target


def execute_bridge_once(actor_repo: Path, queue: Path, workspace_root: Path) -> int:
    bridge = actor_repo / "scripts" / "laptop_actor_github_bridge.py"
    runner = actor_repo / "scripts" / "laptop_actor_runner.py"
    if not bridge.is_file() or not runner.is_file():
        raise RuntimeError("actor checkout is missing bridge or runner")
    proc = subprocess.run(
        [
            sys.executable,
            str(bridge),
            "--queue",
            str(queue),
            "--workspace-root",
            str(workspace_root),
            "--runner",
            str(runner),
            "--once",
        ],
        cwd=actor_repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.returncode


def emit_state(state_dir: Path, state: str, **fields: object) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "state": state,
        **fields,
    }
    path = state_dir / "actor-telemetry.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def acquire_single_instance(state_dir: Path) -> int | None:
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = state_dir / "actor.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.write(fd, str(os.getpid()).encode("ascii"))
    return fd


def release_single_instance(state_dir: Path, fd: int | None) -> None:
    if fd is None:
        return
    os.close(fd)
    try:
        (state_dir / "actor.lock").unlink()
    except FileNotFoundError:
        pass


def run_idle_session(
    actor_repo: Path,
    queue: Path,
    workspace_root: Path,
    min_idle_seconds: int,
    poll_seconds: int,
    state_dir: Path | None = None,
    max_backoff_seconds: int = 900,
) -> int:
    """Run bounded actor cycles while the Windows session remains idle.

    The first cycle is allowed only after the configured idle threshold. After that,
    the actor checks for newly queued work at a short cadence and yields as soon as
    keyboard/mouse activity drops the idle time below the threshold.
    """
    threshold = max(0, min_idle_seconds)
    cadence = max(30, poll_seconds)
    state_dir = state_dir or (queue / "state")
    lock_fd = acquire_single_instance(state_dir)
    if lock_fd is None:
        emit_state(state_dir, "COOLDOWN", reason="single_instance_already_running")
        return 0
    idle = windows_idle_seconds()
    if idle is not None and idle < threshold:
        emit_state(state_dir, "USER_ACTIVE", idle_seconds=idle)
        release_single_instance(state_dir, lock_fd)
        return 0

    failures = 0
    try:
        emit_state(state_dir, "IDLE_AVAILABLE", idle_seconds=idle)
        update_actor_checkout(actor_repo)
        while True:
            idle = windows_idle_seconds()
            if idle is not None and idle < threshold:
                emit_state(state_dir, "USER_ACTIVE", idle_seconds=idle)
                return 0
            emit_state(state_dir, "EXECUTING", idle_seconds=idle)
            rc = execute_bridge_once(actor_repo, queue, workspace_root)
            if rc == 0:
                failures = 0
                emit_state(state_dir, "IDLE_AVAILABLE", result="bridge_cycle_green")
                time.sleep(cadence)
                continue
            failures += 1
            backoff = min(max_backoff_seconds, cadence * (2 ** min(failures - 1, 4)))
            emit_state(state_dir, "COOLDOWN", returncode=rc, backoff_seconds=backoff)
            time.sleep(backoff)
    finally:
        release_single_instance(state_dir, lock_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-updating Exponential Engine laptop actor bootstrap")
    parser.add_argument("--actor-repo", required=True)
    parser.add_argument("--queue", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--min-idle-seconds", type=int, default=300)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--state-dir")
    parser.add_argument("--max-backoff-seconds", type=int, default=900)
    args = parser.parse_args()
    actor_repo = Path(args.actor_repo)
    queue = Path(args.queue)
    workspace_root = Path(args.workspace_root)
    try:
        return run_idle_session(
            actor_repo,
            queue,
            workspace_root,
            args.min_idle_seconds,
            args.poll_seconds,
            Path(args.state_dir) if args.state_dir else None,
            args.max_backoff_seconds,
        )
    except (OSError, RuntimeError):
        # Fail closed. A bootstrap/update failure must never manufacture worker
        # evidence or mutate any product repository.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
