"""Start the network guardian and then the fail-closed persistent supervisor launcher."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def guardian_command(repo: Path, state: Path) -> list[str]:
    return [
        sys.executable,
        str(repo / "scripts" / "network_self_heal.py"),
        "--state-root",
        str(state),
        "--poll-seconds",
        "30",
        "--failures",
        "3",
        "--allow-reboot",
    ]


def launcher_command(
    repo: Path,
    workspace: Path,
    state: Path,
    *,
    boot_owner: bool,
) -> list[str]:
    command = [
        sys.executable,
        str(repo / "scripts" / "launch_persistent_supervisor.py"),
        "--repo-root",
        str(repo),
        "--workspace-root",
        str(workspace),
        "--state-root",
        str(state),
        "--timeout-seconds",
        "60",
        "--builder-seconds",
        "60",
        "--merge-seconds",
        "60",
    ]
    command.append("--yield-on-interactive-session" if boot_owner else "--wait-for-takeover")
    return command


def start_guardian(repo: Path, state: Path) -> None:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        guardian_command(repo, state),
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--boot-owner", action="store_true")
    args = parser.parse_args()

    repo = args.repo_root.resolve()
    workspace = args.workspace_root.resolve()
    state = args.state_root.resolve()

    start_guardian(repo, state)
    result = subprocess.run(
        launcher_command(repo, workspace, state, boot_owner=args.boot_owner),
        cwd=repo,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
