from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .jev_evidence import evaluate_records, load_jsonl
from .jev_reconciliation import records_from_foreman_plan, reconcile_records

DEFAULT_RELATIVE_PATH = Path("telemetry") / "jev-shadow" / "decisions.jsonl"


def _key(record: dict[str, Any]) -> tuple[str, str]:
    return str(record.get("run_id", "")), str(record.get("project_id", ""))


def _atomic_write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def record_foreman_run(path: Path, *, run_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    existing = load_jsonl(path)
    known = {_key(record) for record in existing}
    created = []
    for record in records_from_foreman_plan(plan, run_id=run_id):
        if _key(record) in known:
            continue
        existing.append(record)
        created.append(record)
        known.add(_key(record))
    if created:
        _atomic_write_jsonl(path, existing)
    return {
        "created": len(created),
        "records_total": len(existing),
        "path": str(path),
        "evaluation": evaluate_records(existing),
    }


def reconcile_runtime(path: Path, *, results: list[dict[str, Any]]) -> dict[str, Any]:
    existing = load_jsonl(path)
    if not existing:
        return {
            "closed": 0,
            "records_total": 0,
            "path": str(path),
            "evaluation": evaluate_records([]),
        }
    before = [record.get("outcome") for record in existing]
    updated = reconcile_records(existing, results)
    closed = sum(
        1
        for prior, current in zip(before, updated)
        if prior is None and current.get("outcome") is not None
    )
    if closed:
        _atomic_write_jsonl(path, updated)
    return {
        "closed": closed,
        "records_total": len(updated),
        "path": str(path),
        "evaluation": evaluate_records(updated),
    }


def runtime_cycle(path: Path, *, run_id: str, plan: dict[str, Any],
                  results: list[dict[str, Any]]) -> dict[str, Any]:
    capture = record_foreman_run(path, run_id=run_id, plan=plan)
    reconciliation = reconcile_runtime(path, results=results)
    return {
        "schema_version": 1,
        "mode": "SHADOW_RUNTIME_EVIDENCE",
        "capture": capture,
        "reconciliation": reconciliation,
        "execution_authority": False,
        "promotion_authority": False,
    }
