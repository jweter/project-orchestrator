"""Fail-closed reconstruction of a destroyed grant lifecycle from durable evidence.

This module never replaces the live ledger. It prepares a quarantined preview
so an operator can reconcile each unknown grant without making up outcomes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class LedgerRecoveryRefused(RuntimeError):
    """Source evidence is incomplete or inconsistent."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validated_issue(file: Path) -> tuple[str, dict[str, Any], bytes]:
    raw = file.read_bytes()
    try:
        grant = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise LedgerRecoveryRefused(f"invalid issued grant: {file.name}") from exc
    if not isinstance(grant, dict) or grant.get("grant_id") != file.stem:
        raise LedgerRecoveryRefused(f"grant identity mismatch: {file.name}")
    authority = grant.get("execution_slice")
    products = grant.get("products")
    if not isinstance(authority, dict) or not isinstance(products, dict):
        raise LedgerRecoveryRefused(f"grant authority missing: {file.name}")
    repositories: set[str] = set()
    for product in products.values():
        refs = product.get("repositories") if isinstance(product, dict) else None
        if not isinstance(refs, dict) or not refs:
            raise LedgerRecoveryRefused(f"product authority missing: {file.name}")
        for repo, head in refs.items():
            if not isinstance(repo, str) or not repo or not (
                isinstance(head, str) and len(head) == 40
                and all(c in "0123456789abcdef" for c in head)
            ):
                raise LedgerRecoveryRefused(f"invalid exact-head authority: {file.name}")
            repositories.add(repo)
    if not repositories:
        raise LedgerRecoveryRefused(f"empty authorized repository set: {file.name}")
    return file.stem, {
        "state": "QUARANTINED",
        "recovery_state": "QUARANTINED",
        "result_status": "LEDGER_LOSS_OUTCOME_UNVERIFIED",
        "recovery_result": "RECONCILE_EXACT_HEAD_WORKTREE_LEASE_AND_PR",
        "authorized_repositories": sorted(repositories),
        "repository": authority.get("repository"),
        "execution_slice": authority,
        "runner_id": authority.get("runner_id"),
        "execution_runtime": authority.get("execution_runtime"),
        "expected_sha": authority.get("exact_head"),
        "worker_id": grant.get("worker_id"),
        "source_grant_file": file.name,
        "source_grant_sha256": sha256(raw),
    }, raw


def preview_ledger(
    live: Path, snapshot: Path, issued_dir: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reconstruct known history, quarantine unknown work, change no inputs."""
    if not (live.is_file() and snapshot.is_file() and issued_dir.is_dir()):
        raise LedgerRecoveryRefused("missing source, snapshot or issued-grant directory")
    raw = live.read_bytes()
    if len(raw) < 1024 or any(raw):
        raise LedgerRecoveryRefused("live file is not the verified NUL-filled incident")
    old = snapshot.read_bytes()
    try:
        original = json.loads(old)
    except (ValueError, TypeError) as exc:
        raise LedgerRecoveryRefused("snapshot is unreadable") from exc
    if (not isinstance(original, dict)
            or original.get("schema_version") != 1
            or not isinstance(original.get("grants"), dict)):
        raise LedgerRecoveryRefused("unsupported snapshot schema")
    grants = dict(original["grants"])
    if not grants or any(not isinstance(row, dict) for row in grants.values()):
        raise LedgerRecoveryRefused("historical grant data incomplete")
    historic = len(grants)
    quarantined: list[str] = []
    issued = sorted(issued_dir.glob("*.json"))
    if not issued:
        raise LedgerRecoveryRefused("no per-grant authority sources")
    for file in issued:
        grant_id, record, _ = _validated_issue(file)
        if grant_id in grants:
            continue  # Preserve historical record exactly; never infer an update.
        grants[grant_id] = record
        quarantined.append(grant_id)
    if not quarantined:
        raise LedgerRecoveryRefused("no post-snapshot evidence; manual review required")
    result = {
        "schema_version": 1,
        "grants": grants,
        "forensic_reconstruction": {
            "method": "snapshot_plus_quarantined_issued_authorities",
            "original_sha256": sha256(raw),
            "snapshot_sha256": sha256(old),
            "historical_count": historic,
            "new_unverified_quarantines": len(quarantined),
            "unverified_outcomes_preserved": True,
        },
    }
    stats = {
        "status": "FORENSIC_PREVIEW_ONLY",
        "original_bytes": len(raw),
        "original_sha256": sha256(raw),
        "snapshot_bytes": len(old),
        "snapshot_sha256": sha256(old),
        "historical_count": historic,
        "issued_files": len(issued),
        "new_unverified_quarantines": len(quarantined),
        "total_records": len(grants),
        "live_ledger_modified": False,
    }
    return result, stats


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Read-only forensic grant-ledger audit")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument(
        "--preview-path", type=Path,
        help="write a separate forensic candidate, never the live ledger",
    )
    args = parser.parse_args()
    live = args.state_root / "hands-off-grant-lifecycle.json"
    issued = args.state_root / "hands-off-execution-grants"
    reconstructed, stats = preview_ledger(live, args.snapshot, issued)
    if args.preview_path is not None:
        preview = args.preview_path.resolve()
        if (preview in {live.resolve(), args.snapshot.resolve()}
                or issued.resolve() in preview.parents
                or args.state_root.resolve() in preview.parents):
            raise LedgerRecoveryRefused("forensic output must be outside live state")
        preview.parent.mkdir(parents=True, exist_ok=True)
        if preview.exists():
            raise LedgerRecoveryRefused("existing forensic preview cannot be overwritten")
        encoded = json.dumps(reconstructed, sort_keys=True, indent=2).encode("utf-8")
        with preview.open("xb") as out:
            out.write(encoded)
            out.flush()
        stats["preview_path"] = str(preview)
        stats["preview_sha256"] = sha256(encoded)
        stats["preview_bytes"] = len(encoded)
    print(json.dumps(stats, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
