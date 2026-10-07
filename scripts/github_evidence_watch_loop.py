"""Refresh authoritative GitHub portfolio evidence outside the supervisor critical loop."""
from __future__ import annotations
import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

from project_orchestrator.github_evidence import UrllibGitHubTransport, collect_portfolio
from project_orchestrator.state_io import atomic_json


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--poll-seconds", type=int, default=600)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    repo_root, state_root = Path(args.repo_root), Path(args.state_root)
    while True:
        collect_once(repo_root, state_root)
        if args.once:
            return 0
        time.sleep(max(60, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
