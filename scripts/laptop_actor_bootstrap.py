from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-updating Exponential Engine laptop actor bootstrap")
    parser.add_argument("--actor-repo", required=True)
    parser.add_argument("--queue", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--min-idle-seconds", type=int, default=600)
    args = parser.parse_args()
    actor_repo = Path(args.actor_repo)
    queue = Path(args.queue)
    workspace_root = Path(args.workspace_root)
    try:
        idle = windows_idle_seconds()
        if idle is not None and idle < max(0, args.min_idle_seconds):
            return 0
        update_actor_checkout(actor_repo)
        return execute_bridge_once(actor_repo, queue, workspace_root)
    except (OSError, RuntimeError):
        # Fail closed. A bootstrap/update failure must never manufacture worker
        # evidence or mutate any product repository.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
