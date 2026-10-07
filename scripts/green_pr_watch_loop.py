"""Lightweight always-on green PR watcher. Heavy development remains idle-gated."""

from __future__ import annotations
import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

try:
    from scripts.green_pr_merger import cycle
except ModuleNotFoundError:
    from green_pr_merger import cycle

from project_orchestrator.activation_capability import merge_watcher_advanced
from project_orchestrator.kpi_telemetry import append_event, load_events
from project_orchestrator.planning_dispatch import product_for_repository
from scripts.failed_pr_accountability import cycle as failed_pr_accountability_cycle


def acquire_singleton(state: Path) -> BinaryIO | None:
    """Hold one OS-level lock for the watcher lifetime; duplicates exit cleanly."""
    lock_path = state.with_name(f"{state.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b", buffering=0)
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def release_singleton(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def _project_for_repository(repository: str) -> str:
    project = product_for_repository(repository)
    if project is not None:
        return project
    if repository == "jweter/project-orchestrator":
        return "portfolio"
    raise ValueError(f"unsupported promotion repository: {repository}")


def kpi_events(results: list[dict], observed_at_utc: str, run_id: str) -> list[dict]:
    """Project verified promotion actions into the canonical KPI event contract."""
    events: list[dict] = []
    for result in results:
        repository = result.get("repository")
        pr = result.get("pr")
        head = result.get("head")
        if not isinstance(repository, str) or not isinstance(pr, int) or not isinstance(head, str):
            continue
        if result.get("merged") is True:
            from_state, to_state = "GREEN", "MERGED"
        elif result.get("promoted") is True:
            from_state, to_state = "DRAFT", "GREEN"
        else:
            continue
        events.append(
            {
                "observed_at": observed_at_utc,
                "run_id": run_id,
                "project": _project_for_repository(repository),
                "repository": repository,
                "entity_type": "pr",
                "entity_id": str(pr),
                "from_state": from_state,
                "to_state": to_state,
                "transition_class": "PROMOTION",
                "actor": "ORCHESTRATOR",
                "evidence_refs": [f"github://{repository}/pull/{pr}@{head}"],
                "metadata": {
                    "source": "green_pr_watch_loop",
                    "exact_head_sha": head,
                },
            }
        )
    return events


def append_kpi_events(path: Path, events: list[dict]) -> int:
    if not events:
        return 0
    before = len(load_events(path))
    for event in events:
        append_event(path, event)
    return len(load_events(path)) - before


def process_cycle(state: Path, results: list[dict], error: str | None, started: float) -> dict:
    observed_at = datetime.now(UTC).isoformat()
    payload = {
        "updated_at_utc": observed_at,
        "duration_seconds": round(time.time() - started, 3),
        "error_class": error,
        "results": results,
    }
    atomic(state, payload)

    state_root = state.parent
    run_id = f"green-pr-watcher:{observed_at}"
    events = kpi_events(results, observed_at, run_id)
    event_count = append_kpi_events(state_root / "kpi-events.jsonl", events)

    reconciliation_advanced = False
    if any(event["to_state"] == "MERGED" for event in events):
        reconciliation_advanced = merge_watcher_advanced(state_root)
        if reconciliation_advanced:
            atomic(
                state_root / "continuation-request.json",
                {
                    "requested_at_utc": datetime.now(UTC).isoformat(),
                    "reason": "MERGE_RECONCILED_EVENT",
                },
            )

    payload["kpi_events_emitted"] = event_count
    payload["flow_events_emitted"] = event_count  # compatibility alias during migration
    payload["reconciliation_advanced"] = reconciliation_advanced
    atomic(state, payload)
    return payload


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    p.add_argument("--poll-seconds", type=int, default=60)
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    state = Path(a.state)
    singleton = acquire_singleton(state)
    if singleton is None:
        return 0
    try:
        while True:
            started = time.time()
            try:
                results = cycle(False)
                failed_pr_results = failed_pr_accountability_cycle(Path(__file__).resolve().parents[1], state.parent)
                results.extend(failed_pr_results)
                error = (
                    "PARTIAL_PROMOTION_FAILURE"
                    if any(
                        result.get("decision") in {"REPOSITORY_QUERY_FAILED", "PR_PROCESSING_FAILED"}
                        for result in results
                    ) or (
                        "P1_FAILED_PR_REPAIR_STALLED"
                        if any(result.get("state") in {"REPAIR_ROUTING_FAILED", "OBSERVATION_FAILED"} for result in results)
                        else None
                    )
                    else None
                )
            except Exception as exc:
                results = []
                error = type(exc).__name__
            process_cycle(state, results, error, started)
            if a.once:
                return 0 if error is None else 1
            time.sleep(max(30, a.poll_seconds))
    finally:
        release_singleton(singleton)


if __name__ == "__main__":
    raise SystemExit(main())
