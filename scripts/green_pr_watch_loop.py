"""Lightweight always-on green PR watcher. Heavy development remains idle-gated."""

from __future__ import annotations
import argparse
import hashlib
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


def _event_id(event_type: str, result: dict) -> str:
    material = "\0".join(
        (
            event_type,
            str(result.get("repository") or ""),
            str(result.get("pr") or ""),
            str(result.get("head") or ""),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def lifecycle_events(results: list[dict], observed_at_utc: str) -> list[dict]:
    events: list[dict] = []
    for result in results:
        event_type = None
        if result.get("merged") is True:
            event_type = "PR_MERGED"
        elif result.get("promoted") is True:
            event_type = "PR_READY"
        if event_type is None:
            continue
        event = {
            "schema_version": 1,
            "event_type": event_type,
            "event_id": _event_id(event_type, result),
            "repository": result.get("repository"),
            "pr": result.get("pr"),
            "head": result.get("head"),
            "observed_at_utc": observed_at_utc,
        }
        events.append(event)
    return events


def append_events(path: Path, events: list[dict]) -> int:
    if not events:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_ids: set[str] = set()
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event_id = payload.get("event_id")
            if isinstance(event_id, str):
                existing_ids.add(event_id)
    written = 0
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for event in events:
            if event["event_id"] in existing_ids:
                continue
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            existing_ids.add(event["event_id"])
            written += 1
    return written


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
    events = lifecycle_events(results, observed_at)
    event_count = append_events(state_root / "flow-events.jsonl", events)

    reconciliation_advanced = False
    if any(event["event_type"] == "PR_MERGED" for event in events):
        reconciliation_advanced = merge_watcher_advanced(state_root)
        if reconciliation_advanced:
            atomic(
                state_root / "continuation-request.json",
                {
                    "requested_at_utc": datetime.now(UTC).isoformat(),
                    "reason": "MERGE_RECONCILED_EVENT",
                },
            )

    payload["flow_events_emitted"] = event_count
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
                error = None
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
