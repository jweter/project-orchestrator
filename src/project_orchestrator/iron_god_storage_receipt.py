"""Read-only, bounded storage receipts for Iron God's existing maintenance cycle.

Steward owns machine-health policy. Worktree cleanup owns deletion eligibility.
This module only observes local disk capacity and known engine-owned roots.
It cannot delete, reclassify, or grant ownership of any file.
"""
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .state_io import atomic_json
from .steward import assess_machine_health

RECEIPT_FILENAME = "storage-housekeeping.json"
MAX_SAMPLES = 24
MAX_PRIOR_RECEIPT_BYTES = 128 * 1024
MAX_OWNED_ENTRIES = 2048


def _previous(path: Path) -> dict[str, Any]:
    try:
        # Prior telemetry is untrusted input; never read an arbitrary-size
        # blob or follow a link into unrelated owner-controlled data.
        if path.is_symlink() or path.stat().st_size > MAX_PRIOR_RECEIPT_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _count_owned_entries(path: Path, *, directories: bool) -> dict[str, Any]:
    """Count only direct, non-link children; never traverse unclassified data."""
    try:
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            return {"status": "LINK_PROTECTED", "count": None}
        if not path.exists():
            return {"status": "ABSENT", "count": 0}
        if not path.is_dir():
            return {"status": "UNEXPECTED_PATH_TYPE", "count": None}
        count = 0
        for scanned, entry in enumerate(path.iterdir(), start=1):
            if scanned > MAX_OWNED_ENTRIES:
                return {"status": "SCAN_LIMIT_REACHED", "count": None}
            if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                continue
            if entry.is_dir() if directories else entry.is_file():
                count += 1
        return {"status": "OBSERVED", "count": count}
    except OSError:
        return {"status": "UNREADABLE", "count": None}


def record_storage_receipt(
    state_root: Path, cleanup_report: dict[str, Any] | None,
    *, cleanup_error: str | None = None,
) -> dict[str, Any]:
    """Record bounded disk history, not an unverifiable 'bytes reclaimed' claim.

    Free-space delta includes other processes' disk activity and is expressly
    observational. Cleanup code and its owner/worker safety gates are unchanged.
    """
    root = Path(state_root)
    path = root / "iron-god-products" / "reports" / RECEIPT_FILENAME
    prior = _previous(path)
    observed_at = datetime.now(UTC).isoformat()
    try:
        usage = shutil.disk_usage(root)
        free = int(usage.free)
        total = int(usage.total)
        percent = round(100.0 * (total - free) / total, 2) if total else None
        disk = {
            "status": "OBSERVED",
            "volume": str(root.anchor),
            "free_bytes": free,
            "total_bytes": total,
            "used_percent": percent,
        }
        assessment = assess_machine_health({
            "disks": [{
                "name": str(root.anchor),
                "free_gb": free / 2 ** 30,
                "used_percent": percent,
            }]
        })
        storage = next(
            (dimension for dimension in assessment["dimensions"]
             if dimension["dimension"] == "storage"), None
        )
        disk["band"] = storage["band"] if storage is not None else "UNKNOWN"
    except (OSError, ValueError):
        disk = {
            "status": "UNAVAILABLE",
            "volume": str(root.anchor),
            "free_bytes": None,
            "total_bytes": None,
            "used_percent": None,
            "band": "UNKNOWN",
        }

    prior_disk = prior.get("disk")
    old_disk: dict[str, Any] = prior_disk if isinstance(prior_disk, dict) else {}
    before = old_disk.get("free_bytes")
    now_free = disk.get("free_bytes")
    delta = (
        now_free - before
        if isinstance(before, int) and isinstance(now_free, int)
        and old_disk.get("volume") == disk.get("volume")
        else None
    )
    counts = {
        "product_worktrees": _count_owned_entries(
            root.parent / "canon-workspaces" / "products" / "worktrees", directories=True
        ),
        "recovery_archive_files": _count_owned_entries(
            root / "iron-god-products" / "reports" / "worktree-archive", directories=False
        ),
    }
    if cleanup_error is not None:
        summary = {"status": "FAILED", "reason": cleanup_error[:200]}
    elif cleanup_report is None:
        summary = {"status": "NOT_DUE"}
    else:
        summary = {
            "status": "REPORTED",
            **{key: cleanup_report[key] for key in (
                "worktrees", "eligible", "removed", "left_for_next_run", "results"
            ) if key in cleanup_report},
        }
    previous_samples = prior.get("samples")
    samples = (
        [sample for sample in previous_samples if isinstance(sample, dict)]
        if isinstance(previous_samples, list) else []
    )
    samples = samples[-(MAX_SAMPLES - 1):]
    samples.append({
        "at_utc": observed_at, "free_bytes": now_free,
        "disk_band": disk["band"],
    })
    result = {
        "schema_version": 1,
        "observed_at_utc": observed_at,
        "disk": disk,
        "observed_free_delta_bytes": delta,
        "delta_is_not_cleanup_attribution": True,
        "counts": counts,
        "last_cleanup": summary,
        "samples": samples,
        "mode": "READ_ONLY_ACCOUNTING_NO_DELETION",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, result)
    return result
