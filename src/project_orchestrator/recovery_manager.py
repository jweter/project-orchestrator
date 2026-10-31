from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .grant_lifecycle import (
    acquire_lifecycle_lock,
    atomic_lifecycle_json,
    release_lifecycle_lock,
)

ISSUE_LEASE_SECONDS = 60
RECOVERY_GENERATION_LEASE_SECONDS = 60
CLAIM_LEASE_SECONDS = 1800
UNKNOWN_LIVENESS_MAX_CHECKS = 5

LivenessProbe = Callable[[dict | None], str]


def process_start_fingerprint(pid: int | None = None) -> str | None:
    """Return a lightweight OS-backed process creation identity."""
    pid = pid or os.getpid()
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            start_ticks = stat[stat.rfind(")") + 2:].split()[19]
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
            return f"linux:{boot_id}:{start_ticks}"
        except (OSError, IndexError, ValueError):
            return None
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            try:
                if not kernel32.GetProcessTimes(
                    handle, ctypes.byref(creation), ctypes.byref(exit_time),
                    ctypes.byref(kernel), ctypes.byref(user),
                ):
                    return None
                ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                return f"windows:{ticks}"
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return None
    return None


def _windows_pid_liveness(pid: int) -> str:
    """Check a Windows process without sending it a signal."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return "DEAD" if ctypes.get_last_error() in {87, 1168} else "UNKNOWN"
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return "UNKNOWN"
            return "ALIVE" if exit_code.value == 259 else "DEAD"
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        return "UNKNOWN"


def worker_liveness(worker: dict | None) -> str:
    """Return ALIVE, DEAD, or UNKNOWN without guessing across hosts."""
    if not isinstance(worker, dict):
        return "UNKNOWN"
    if worker.get("host") != socket.gethostname():
        return "UNKNOWN"
    try:
        pid = int(worker["pid"])
    except (KeyError, TypeError, ValueError):
        return "UNKNOWN"
    if pid <= 0:
        return "UNKNOWN"
    # On Windows, os.kill(pid, 0) is destructive; use a read-only handle.
    if sys.platform == "win32":
        existence = _windows_pid_liveness(pid)
        if existence != "ALIVE":
            return existence
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "DEAD"
        except PermissionError:
            pass
        except OSError:
            return "UNKNOWN"
    expected = worker.get("process_start_fingerprint")
    if expected:
        try:
            from scripts.autonomous_slice_builder import process_start_fingerprint as builder_process_start_fingerprint
            actual = builder_process_start_fingerprint(pid)
        except (ImportError, OSError):
            return "UNKNOWN"
        if actual is None:
            return "UNKNOWN"
        if actual != expected:
            return "DEAD"
    return "ALIVE"


def recover_stale_issued_grants(
    state_root: Path,
    now: datetime | None,
    liveness_probe: LivenessProbe,
) -> list[str]:
    path = state_root / "hands-off-grant-lifecycle.json"
    lock = acquire_lifecycle_lock(path)
    try:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            grants = payload.get("grants", {})
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("GRANT_LIFECYCLE_UNREADABLE") from exc
        current = now or datetime.now(UTC)
        recovered: list[str] = []
        changed = False
        for grant_id, record in grants.items():
            if not isinstance(record, dict) or record.get("state") != "ISSUED":
                continue
            try:
                issued = datetime.fromisoformat(record["issued_at"])
                if issued.tzinfo is None:
                    issued = issued.replace(tzinfo=UTC)
            except (KeyError, TypeError, ValueError):
                continue
            if (current - issued).total_seconds() <= ISSUE_LEASE_SECONDS:
                continue
            liveness = liveness_probe(record.get("issuer"))
            record["last_issue_liveness_check_at"] = current.isoformat()
            record["issuer_liveness"] = liveness
            changed = True
            if liveness == "DEAD":
                record["state"] = "ABANDONED"
                record["finished_at"] = current.isoformat()
                record["result_status"] = "ISSUE_LEASE_EXPIRED_ISSUER_DEAD"
                recovered.append(grant_id)
            elif liveness == "UNKNOWN":
                checks = int(record.get("issue_unknown_liveness_checks", 0)) + 1
                record["issue_unknown_liveness_checks"] = checks
                if checks >= UNKNOWN_LIVENESS_MAX_CHECKS:
                    record["state"] = "QUARANTINED"
                    record["finished_at"] = current.isoformat()
                    record["result_status"] = "ISSUE_LEASE_UNKNOWN_QUARANTINED"
            else:
                record["issue_unknown_liveness_checks"] = 0
                record["result_status"] = "ISSUE_LEASE_EXPIRED_ISSUER_ALIVE"
        if changed:
            atomic_lifecycle_json(path, payload)
        return recovered
    finally:
        release_lifecycle_lock(lock)


def recover_stale_recovery_generations(
    state_root: Path,
    now: datetime | None,
    liveness_probe: LivenessProbe,
) -> list[str]:
    """Release recovery generations only when their owner is proven dead."""
    path = state_root / "hands-off-grant-lifecycle.json"
    lock = acquire_lifecycle_lock(path)
    try:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            grants = payload.get("grants", {})
        except (OSError, json.JSONDecodeError, TypeError):
            return []
        current = now or datetime.now(UTC)
        released: list[str] = []
        changed = False
        for grant_id, record in grants.items():
            if not isinstance(record, dict) or not record.get("recovery_generation"):
                continue
            try:
                started = datetime.fromisoformat(record["recovery_generation_started_at"])
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
            except (KeyError, TypeError, ValueError):
                record["recovery_generation_liveness"] = "UNKNOWN"
                record["recovery_generation_result"] = "RECOVERY_GENERATION_TIMESTAMP_INVALID"
                record["recovery_state"] = "QUARANTINED"
                record["recovery_quarantine_reason"] = "RECOVERY_GENERATION_TIMESTAMP_INVALID"
                changed = True
                continue
            if (current - started).total_seconds() <= RECOVERY_GENERATION_LEASE_SECONDS:
                continue
            liveness = liveness_probe(record.get("recovery_generation_worker"))
            record["recovery_generation_last_liveness_check_at"] = current.isoformat()
            record["recovery_generation_liveness"] = liveness
            changed = True
            if liveness == "DEAD":
                record.pop("recovery_generation", None)
                record.pop("recovery_generation_started_at", None)
                record.pop("recovery_generation_worker", None)
                record["recovery_generation_released_at"] = current.isoformat()
                record["recovery_generation_result"] = "RECOVERY_GENERATION_OWNER_DEAD"
                # This recovery generation is proven dead and no longer owns
                # the repository. Only an independently durable PENDING
                # transport obligation reserves ownership before generation
                # acquisition; a dead RECOVERING owner must release it.
                if record.get("recovery_state") == "RECOVERING":
                    # Release only the dead recovery generation. The verified
                    # promotion obligation still owns this repository until a
                    # fresh recovery generation acquires it or promotion ends.
                    record["recovery_state"] = "PENDING"
                released.append(grant_id)
            elif liveness == "ALIVE":
                record["recovery_generation_result"] = "RECOVERY_GENERATION_OWNER_ALIVE"
            else:
                checks = int(record.get("recovery_generation_unknown_checks", 0)) + 1
                record["recovery_generation_unknown_checks"] = checks
                record["recovery_generation_result"] = "RECOVERY_GENERATION_OWNER_UNKNOWN"
                if checks >= UNKNOWN_LIVENESS_MAX_CHECKS:
                    record["recovery_state"] = "QUARANTINED"
                    record["recovery_quarantine_reason"] = "RECOVERY_GENERATION_OWNER_UNKNOWN"
        if changed:
            atomic_lifecycle_json(path, payload)
        return released
    finally:
        release_lifecycle_lock(lock)


def recover_stale_grant_claims(
    state_root: Path,
    now: datetime | None,
    liveness_probe: LivenessProbe,
) -> list[str]:
    """Abandon an expired claim only when its owning worker is proven dead."""
    path = state_root / "hands-off-grant-lifecycle.json"
    lock = acquire_lifecycle_lock(path)
    try:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            grants = payload.get("grants", {})
        except (OSError, json.JSONDecodeError, TypeError):
            return []
        current = now or datetime.now(UTC)
        abandoned: list[str] = []
        changed = False
        for grant_id, record in grants.items():
            if not isinstance(record, dict) or record.get("state") != "CLAIMED":
                continue
            try:
                heartbeat = datetime.fromisoformat(record.get("last_heartbeat_at", record["claimed_at"]))
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=UTC)
            except (KeyError, TypeError, ValueError):
                continue
            claim_expired = (current - heartbeat).total_seconds() > CLAIM_LEASE_SECONDS
            # A positively identified dead local worker cannot still own useful
            # work. Probe before the lease-age gate so that its lane is released
            # immediately. ALIVE and UNKNOWN owners retain the existing lease
            # protections and fail-closed timeout behavior.
            liveness = liveness_probe(record.get("worker"))
            if liveness != "DEAD" and not claim_expired:
                continue
            record["last_liveness_check_at"] = current.isoformat()
            record["worker_liveness"] = liveness
            changed = True
            if liveness == "DEAD":
                record["state"] = "ABANDONED"
                record["finished_at"] = current.isoformat()
                record["result_status"] = (
                    "CLAIM_WORKER_DEAD_FAST_RECOVERY"
                    if not claim_expired else "CLAIM_LEASE_EXPIRED_WORKER_DEAD"
                )
                abandoned.append(grant_id)
            elif liveness == "UNKNOWN":
                checks = int(record.get("unknown_liveness_checks", 0)) + 1
                record["unknown_liveness_checks"] = checks
                record.setdefault("unknown_liveness_first_seen_at", current.isoformat())
                if checks >= UNKNOWN_LIVENESS_MAX_CHECKS:
                    record["state"] = "QUARANTINED"
                    record["finished_at"] = current.isoformat()
                    record["result_status"] = "CLAIM_LEASE_UNKNOWN_QUARANTINED"
                else:
                    record["result_status"] = "CLAIM_LEASE_EXPIRED_WORKER_UNKNOWN"
            else:
                record["unknown_liveness_checks"] = 0
                record.pop("unknown_liveness_first_seen_at", None)
                record["result_status"] = "CLAIM_LEASE_EXPIRED_WORKER_ALIVE"
        if changed:
            atomic_lifecycle_json(path, payload)
        return abandoned
    finally:
        release_lifecycle_lock(lock)


def recheck_quarantined_claims(
    state_root: Path,
    now: datetime | None,
    liveness_probe: LivenessProbe,
) -> dict[str, str]:
    """Re-evaluate ownership quarantine; only proof of death authorizes recovery."""
    path = state_root / "hands-off-grant-lifecycle.json"
    lock = acquire_lifecycle_lock(path)
    try:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            grants = payload.get("grants", {})
        except (OSError, json.JSONDecodeError, TypeError):
            return {}
        current = now or datetime.now(UTC)
        outcomes: dict[str, str] = {}
        changed = False
        for grant_id, record in grants.items():
            if not isinstance(record, dict) or record.get("state") != "QUARANTINED":
                continue
            result_status = record.get("result_status")
            if result_status not in {"CLAIM_LEASE_UNKNOWN_QUARANTINED", "ISSUE_LEASE_UNKNOWN_QUARANTINED"}:
                continue
            if record.get("recovery_generation"):
                outcomes[grant_id] = "RECOVERY_GENERATION_ACTIVE"
                continue
            owner_field = "issuer" if result_status == "ISSUE_LEASE_UNKNOWN_QUARANTINED" else "worker"
            liveness = liveness_probe(record.get(owner_field))
            record["quarantine_last_recheck_at"] = current.isoformat()
            record["quarantine_rechecks"] = int(record.get("quarantine_rechecks", 0)) + 1
            record["worker_liveness"] = liveness
            changed = True
            if liveness == "DEAD":
                record["state"] = "ABANDONED"
                record["result_status"] = (
                    "ISSUE_LEASE_QUARANTINE_ISSUER_DEAD"
                    if owner_field == "issuer" else "CLAIM_LEASE_QUARANTINE_WORKER_DEAD"
                )
                record["finished_at"] = current.isoformat()
                outcomes[grant_id] = "RELEASED_TO_RECOVERY"
            elif liveness == "ALIVE":
                record["state"] = "ISSUED" if owner_field == "issuer" else "CLAIMED"
                record["result_status"] = (
                    "ISSUE_LEASE_QUARANTINE_ISSUER_ALIVE"
                    if owner_field == "issuer" else "CLAIM_LEASE_QUARANTINE_WORKER_ALIVE"
                )
                counter = "issue_unknown_liveness_checks" if owner_field == "issuer" else "unknown_liveness_checks"
                record[counter] = 0
                record.pop("unknown_liveness_first_seen_at", None)
                record.pop("finished_at", None)
                outcomes[grant_id] = "RESTORED_TO_ISSUED" if owner_field == "issuer" else "RESTORED_TO_CLAIMED"
            else:
                outcomes[grant_id] = "STILL_UNKNOWN"
        if changed:
            atomic_lifecycle_json(path, payload)
        return outcomes
    finally:
        release_lifecycle_lock(lock)


def quarantined_grants(state_root: Path) -> dict[str, dict]:
    """Expose safe actionable quarantine evidence without lease tokens/secrets."""
    path = state_root / "hands-off-grant-lifecycle.json"
    if not path.exists():
        return {}
    try:
        grants = json.loads(path.read_text(encoding="utf-8")).get("grants", {})
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    result: dict[str, dict] = {}
    for grant_id, record in grants.items():
        if not isinstance(record, dict):
            continue
        if record.get("state") == "QUARANTINED" or record.get("recovery_state") == "QUARANTINED":
            worker = record.get("worker") if isinstance(record.get("worker"), dict) else {}
            result[grant_id] = {
                "repository": record.get("repository"),
                "result_status": record.get("result_status"),
                "recovery_result": record.get("recovery_result"),
                "unknown_liveness_checks": record.get("unknown_liveness_checks", 0),
                "worker_host": worker.get("host"),
                "worker_pid": worker.get("pid"),
                "process_start_fingerprint": worker.get("process_start_fingerprint"),
                "finished_at": record.get("finished_at"),
            }
    return result


def _git_text(repo_root: Path, *args: str) -> str | None:
    completed = subprocess.run(
        ["git", *args], cwd=repo_root, text=True, capture_output=True, check=False
    )
    return completed.stdout if completed.returncode == 0 else None


def _verified_durable_preservation(record: dict, proof: dict) -> str | None:
    evidence = record.get("preserved_work_evidence")
    if (
        not isinstance(evidence, dict)
        or evidence.get("capture_status") != "CAPTURED"
        or record.get("preserved_work_revalidation") != "PRESERVED_WORK_EXACT"
    ):
        return None
    try:
        repo_root = Path(proof["repository_path"]).resolve()
        commit_hint = str(proof["commit_sha"])
        remote_ref = str(proof["remote_ref"])
    except (KeyError, TypeError, OSError):
        return None
    if not repo_root.is_dir() or not remote_ref.startswith("origin/recovery/"):
        return None
    origin = _git_text(repo_root, "remote", "get-url", "origin")
    repository = str(record.get("repository") or "")
    if not origin or not repository:
        return None
    normalized_origin = origin.strip().replace("git@github.com:", "https://github.com/")
    if normalized_origin.endswith(".git"):
        normalized_origin = normalized_origin[:-4]
    if not normalized_origin.endswith("/" + repository):
        return None
    commit = _git_text(repo_root, "rev-parse", f"{commit_hint}^{{commit}}")
    remote_commit = _git_text(repo_root, "rev-parse", remote_ref)
    if not commit or not remote_commit or commit.strip() != remote_commit.strip():
        return None
    commit = commit.strip()
    base = str(record.get("base_sha") or "")
    diff = _git_text(repo_root, "diff", "--binary", base, commit)
    names = _git_text(repo_root, "diff", "--name-only", base, commit)
    if diff is None or names is None:
        return None
    payload = diff.encode("utf-8", errors="replace")
    if hashlib.sha256(payload).hexdigest() != evidence.get("diff_sha256"):
        return None
    if len(payload) != evidence.get("diff_bytes"):
        return None
    if [p for p in names.splitlines() if p] != evidence.get("changed_paths"):
        return None
    return commit


def terminalize_abandoned_preclaim_quarantines(
    state_root: Path,
    now: datetime | None = None,
    preserved_proofs: dict[str, dict] | None = None,
) -> dict[str, str]:
    """Release only quarantines with proof that ownership can end safely."""
    path = state_root / "hands-off-grant-lifecycle.json"
    lock = acquire_lifecycle_lock(path)
    try:
        # Imported here: human_ownership -> iron_god_owner_control imports this
        # module, so a top-level import would be circular.
        from .human_ownership import human_mode_active

        if not path.exists() or human_mode_active(state_root):
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            grants = payload.get("grants", {})
        except (OSError, json.JSONDecodeError, TypeError):
            return {}
        current = now or datetime.now(UTC)
        outcomes: dict[str, str] = {}
        changed = False
        preserved_proofs = preserved_proofs or {}
        verified_preserved: dict[str, str] = {}
        for grant_id, proof in preserved_proofs.items():
            record = grants.get(grant_id)
            if (
                not isinstance(record, dict)
                or record.get("state") != "ABANDONED"
                or record.get("recovery_state") != "QUARANTINED"
                or record.get("result_status") != "CLAIM_LEASE_EXPIRED_WORKER_DEAD"
                or not isinstance(proof, dict)
            ):
                return {grant_id: "PRESERVATION_PROOF_REJECTED"}
            commit = _verified_durable_preservation(record, proof)
            if not commit:
                return {grant_id: "PRESERVATION_PROOF_REJECTED"}
            verified_preserved[grant_id] = commit
        for grant_id, record in grants.items():
            if not isinstance(record, dict):
                continue
            if record.get("state") != "ABANDONED" or record.get("recovery_state") != "QUARANTINED":
                continue
            result_status = str(record.get("result_status") or "")
            if result_status == "CLAIM_LEASE_EXPIRED_WORKER_DEAD":
                proof = preserved_proofs.get(grant_id)
                if not isinstance(proof, dict):
                    continue
                commit = verified_preserved[grant_id]
                record["recovery_state"] = "RECOVERED"
                record["recovery_result"] = "PRESERVED_AS_DURABLE_REMOTE_RECOVERY_COMMIT"
                record["preserved_recovery_commit"] = commit
                record["preserved_recovery_ref"] = proof["remote_ref"]
                record["recovered_at"] = current.isoformat()
                record.pop("recovery_next_attempt_at", None)
                outcomes[grant_id] = "TERMINALIZED_DURABLY_PRESERVED_WORK"
                changed = True
                continue
            if not result_status.startswith("BUILDER_EXITED_WITHOUT_CLAIM:"):
                continue
            if any((
                record.get("repository"),
                record.get("branch"),
                record.get("head_sha"),
                record.get("workspace_path"),
                record.get("preserved_work_evidence"),
                record.get("recovery_generation"),
            )):
                outcomes[grant_id] = "PRESERVED_OR_AMBIGUOUS_QUARANTINE"
                continue
            record["recovery_state"] = "RECOVERED"
            record["recovery_result"] = "TERMINAL_PRECLAIM_NO_PRODUCT_WORK"
            record["recovered_at"] = current.isoformat()
            record.pop("recovery_next_attempt_at", None)
            outcomes[grant_id] = "TERMINALIZED_PRECLAIM_NO_PRODUCT_WORK"
            changed = True
        if changed:
            atomic_lifecycle_json(path, payload)
        return outcomes
    finally:
        release_lifecycle_lock(lock)
