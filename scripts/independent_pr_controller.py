"""Independent fail-closed PR promotion/failed-CI controller for Azathoth.

Runs as one scheduled, non-overlapping pass; does not require the dev supervisor.
Existing repository-native CI, review, and exact-head merge gates are reused.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# Scheduled Task executes this file by path; ensure src-layout imports resolve.
_repo_root = Path(__file__).resolve().parents[1]
for _root in (str(_repo_root), str(_repo_root / 'src')):
    if _root not in sys.path:
        sys.path.insert(0, _root)

from scripts import green_pr_merger, green_pr_watch_loop
from scripts.failed_pr_accountability import cycle as failed_pr_cycle


class OwnerControlVeto(RuntimeError):
    pass


def owner_status(state_root: Path) -> str:
    """Only the authoritative owner controller can allow external work."""
    from project_orchestrator.human_ownership import owner_control_status

    return owner_control_status(state_root)


def reconcile(repo_root: Path, state_root: Path) -> dict:
    initial = owner_status(state_root)
    if initial != "AUTHORIZED":
        return {"status": initial, "acted": False}
    output = state_root / "green-pr-watcher.json"
    # Share the *same kernel lock* with the existing watcher; no duplicate merges.
    singleton = green_pr_watch_loop.acquire_singleton(output)
    if singleton is None:
        return {"status": "ALREADY_RUNNING", "acted": False}
    real_run = subprocess.run

    def guarded_run(*args, **kwargs):
        status = owner_status(state_root)
        if status != "AUTHORIZED":
            raise OwnerControlVeto(status)
        # Never let gh.exe or Python repair children open a console over Rocksmith.
        if sys.platform == "win32":
            kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return real_run(*args, **kwargs)

    # The current conservative merger and CI router both invoke subprocess.run.
    # Re-check the marker immediately before EVERY gh/child command, not just
    # once at startup. This dedicated process has no other Python workers.
    subprocess.run = guarded_run
    started = time.monotonic()
    try:
        if owner_status(state_root) != "AUTHORIZED":
            return {"status": owner_status(state_root), "acted": False}
        decisions = green_pr_merger.cycle(False)
        if owner_status(state_root) != "AUTHORIZED":
            return {"status": owner_status(state_root), "acted": False}
        decisions.extend(failed_pr_cycle(repo_root, state_root))
        if owner_status(state_root) != "AUTHORIZED":
            return {"status": owner_status(state_root), "acted": False}
        failures = [d for d in decisions if d.get("decision") in {
            "REPOSITORY_QUERY_FAILED", "PR_PROCESSING_FAILED"
        } or d.get("state") in {"OBSERVATION_FAILED", "REPAIR_ROUTING_FAILED"}]
        receipt = green_pr_watch_loop.process_cycle(
            output, decisions, "PARTIAL_RECONCILIATION_FAILURE" if failures else None,
            time.time() - (time.monotonic() - started),
        )
        return {"status": "RECONCILED", "acted": True, "checks": len(decisions),
                "errors": len(failures), "kpi_events": receipt.get("kpi_events_emitted", 0)}
    except OwnerControlVeto as exc:
        return {"status": str(exc), "acted": False}
    finally:
        subprocess.run = real_run
        green_pr_watch_loop.release_singleton(singleton)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = reconcile(args.repo_root.resolve(), args.state_root.resolve())
    print(json.dumps(result, sort_keys=True))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
