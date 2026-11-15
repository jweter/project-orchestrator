"""Inspect an authorized repair-advice receipt and stage *review-only* backlog intake.

This cannot import/approve work. The existing owner controller grants access,
not CLI flags, mailbox messages or the advice file.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a non-dispatchable Iron God repair intake for owner review"
    )
    parser.add_argument("--lane", required=True, type=Path)
    parser.add_argument("--release-repo", required=True, type=Path)
    parser.add_argument("--clone", required=True, type=Path)
    parser.add_argument("--advice-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT / "src"))
    from project_orchestrator import iron_god_backlog as backlog
    from project_orchestrator.execution_authority import evaluate_execution_authority
    from project_orchestrator.iron_god_microtask_repair_intake import stage_for_review

    release_sha = os.environ.get("IRON_GOD_ACCEPTED_SHA")

    def authorized() -> bool:
        if evaluate_execution_authority(release_sha=release_sha).permitted is not True:
            return False
        try:
            current = subprocess.run(
                ["git", "-C", str(args.clone), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return False
        return current == args.head

    if not authorized():
        result = {"status": "OWNER_AUTHORITY_OR_EXACT_HEAD_DENIED"}
    else:
        items, rejected = backlog.load_backlog(args.release_repo)
        if rejected:
            result = {"status": "BACKLOG_INVALID", "rejected_ids": sorted(rejected)}
        else:
            try:
                state = backlog.load_state(args.lane)
            except ValueError:
                result = {"status": "BACKLOG_LEDGER_UNVERIFIED"}
            else:
                result = stage_for_review(
                    args.lane,
                    args.advice_sha,
                    args.repository,
                    args.head,
                    items=items,
                    state=state,
                    authority=authorized,
                )
    print(
        json.dumps(
            {
                "status": result["status"],
                "path": result.get("path"),
                "review_only": True,
                "dispatchable": False,
            },
            sort_keys=True,
        )
    )
    return 0 if result["status"] in {"REVIEW_PACKAGE_STAGED", "ALREADY_STAGED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
