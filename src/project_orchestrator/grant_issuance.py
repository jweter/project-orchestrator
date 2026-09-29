from __future__ import annotations

import json
import os
import socket
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable

from .grant_lifecycle import acquire_lifecycle_lock, release_lifecycle_lock
from .state_io import atomic_json

PathConflict = Callable[[dict[str, Any], dict[str, Any]], bool]


def conflicting_nonterminal_grant(
    state_root: Path,
    repo: str,
    execution_slice: dict[str, Any],
    paths_conflict: PathConflict,
) -> dict[str, Any] | None:
    path = state_root / "hands-off-grant-lifecycle.json"
    if not path.exists():
        return None
    for attempt in range(5):
        try:
            grants = json.loads(path.read_text(encoding="utf-8")).get("grants", {})
            break
        except PermissionError as exc:
            if attempt == 4:
                raise RuntimeError("GRANT_LIFECYCLE_UNREADABLE") from exc
            time.sleep(0.05 * (attempt + 1))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("GRANT_LIFECYCLE_UNREADABLE") from exc
    for grant_id, record in grants.items():
        if not isinstance(record, dict):
            continue
        owns = record.get("state") in {"ISSUED", "CLAIMED", "QUARANTINED"} or bool(record.get("recovery_generation"))
        if not owns:
            continue
        authority = record.get("execution_slice") or {}
        if repo in record.get("authorized_repositories", []) and paths_conflict(execution_slice, authority):
            return {
                "grant_id": grant_id,
                "state": record.get("state"),
                "worker": record.get("worker"),
                "execution_slice": authority,
            }
    return None


def _acquire_os_file_lock(handle: BinaryIO) -> bool:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, BlockingIOError):
        return False


def _release_os_file_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire_grant_issue_lock(state_root: Path, process_fingerprint: str | None) -> dict[str, Any]:
    state_root.mkdir(parents=True, exist_ok=True)
    path = state_root / "grant-issuance.lock"
    handle = path.open("a+b", buffering=0)
    if not _acquire_os_file_lock(handle):
        handle.close()
        raise RuntimeError("GRANT_ISSUANCE_BUSY")
    worker = {
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "incarnation_id": uuid.uuid4().hex,
        "process_start_fingerprint": process_fingerprint,
        "lease_token": uuid.uuid4().hex,
    }
    lock_id = str(uuid.uuid4())
    metadata = state_root / "grant-issuance.lock.json"
    atomic_json(metadata, {
        "schema_version": 2,
        "lock_id": lock_id,
        "worker": worker,
        "created_at_utc": datetime.now(UTC).isoformat(),
    })
    return {"path": path, "metadata_path": metadata, "handle": handle, "lock_id": lock_id, "worker": worker}


def release_grant_issue_lock(lock: dict[str, Any]) -> None:
    handle = lock["handle"]
    metadata = lock["metadata_path"]
    error: RuntimeError | None = None
    try:
        if metadata.exists():
            try:
                current = json.loads(metadata.read_text(encoding="utf-8"))
                if current.get("lock_id") != lock.get("lock_id"):
                    error = RuntimeError("GRANT_ISSUANCE_LOCK_OWNERSHIP_LOST")
                else:
                    metadata.unlink()
            except (OSError, json.JSONDecodeError, TypeError):
                error = RuntimeError("GRANT_ISSUANCE_LOCK_METADATA_UNREADABLE")
    finally:
        try:
            _release_os_file_lock(handle)
        finally:
            handle.close()
    if error is not None:
        raise error


def reserve_execution_grant(
    state_root: Path,
    grant: dict[str, Any],
    repo: str,
    exact_head: str,
    execution_slice: dict[str, Any],
    runner_id: str,
    runtime_id: str,
    planner_id: str | None,
    issuer: dict[str, Any],
    paths_conflict: PathConflict,
) -> Path:
    grant_id = str(grant["grant_id"])
    path = state_root / "hands-off-execution-grants" / f"{grant_id}.json"
    lifecycle = state_root / "hands-off-grant-lifecycle.json"
    lifecycle_lock = acquire_lifecycle_lock(lifecycle)
    try:
        try:
            payload = json.loads(lifecycle.read_text(encoding="utf-8")) if lifecycle.exists() else {"schema_version": 1, "grants": {}}
            grants = payload.setdefault("grants", {})
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("GRANT_LIFECYCLE_UNREADABLE") from exc
        for existing_id, existing in grants.items():
            if not isinstance(existing, dict):
                continue
            owns = existing.get("state") in {"ISSUED", "CLAIMED", "QUARANTINED"} or bool(existing.get("recovery_generation"))
            existing_repos = set(existing.get("authorized_repositories", []))
            if existing.get("repository"):
                existing_repos.add(existing["repository"])
            existing_slice = existing.get("execution_slice") or {}
            if owns and repo in existing_repos and paths_conflict(execution_slice, existing_slice):
                raise RuntimeError(f"EXECUTION_SLICE_ALREADY_OWNED:{existing_id}:{existing.get('state')}")
        issued = datetime.now(UTC).isoformat()
        authorized_repositories = sorted({
            granted_repo
            for value in grant["products"].values()
            for granted_repo in value["repositories"]
        })
        grants[grant_id] = {
            "state": "ISSUED",
            "issued_at": issued,
            "authorized_repositories": authorized_repositories,
            "expected_sha": exact_head,
            "capability_id": execution_slice["capability_id"],
            "slice_id": execution_slice["slice_id"],
            "runner_id": runner_id,
            "execution_runtime": runtime_id,
            "execution_slice": execution_slice,
            "planner_decision_id": planner_id,
            "issuer": issuer,
        }
        atomic_json(path, grant)
        atomic_json(lifecycle, payload)
        return path
    finally:
        release_lifecycle_lock(lifecycle_lock)


def finalize_unclaimed_grant(grant_path: Path, returncode: int, builder_state: Path | None = None) -> bool:
    lifecycle = (
        grant_path.parent.parent / "hands-off-grant-lifecycle.json"
        if grant_path.parent.name == "hands-off-execution-grants"
        else grant_path.with_name("hands-off-grant-lifecycle.json")
    )
    lifecycle_lock = acquire_lifecycle_lock(lifecycle)
    try:
        try:
            grant_id = json.loads(grant_path.read_text(encoding="utf-8"))["grant_id"]
            payload = json.loads(lifecycle.read_text(encoding="utf-8"))
            record = payload["grants"][grant_id]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return False
        if record.get("state") != "ISSUED":
            return False
        record["state"] = "ABANDONED"
        record["finished_at"] = datetime.now(UTC).isoformat()
        failure_status = None
        failure_class = None
        if builder_state is not None:
            try:
                builder = json.loads(builder_state.read_text(encoding="utf-8"))
                failure_status = builder.get("status")
                failure_class = builder.get("error_class")
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        suffix = f":{failure_status}" if failure_status else ""
        if failure_class:
            suffix += f":{failure_class}"
        record["result_status"] = f"BUILDER_EXITED_WITHOUT_CLAIM:{returncode}{suffix}"
        atomic_json(lifecycle, payload)
        return True
    finally:
        release_lifecycle_lock(lifecycle_lock)
