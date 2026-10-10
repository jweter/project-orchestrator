"""Lock-protected publication for a reconciled NUL-filled grant ledger incident."""
from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .grant_lifecycle import acquire_lifecycle_lock, atomic_lifecycle_json, release_lifecycle_lock
from .ledger_forensics import LedgerRecoveryRefused, preview_ledger

OWNING_STATES = {"ISSUED", "CLAIMED", "RUNNING", "QUARANTINED"}


class RecoveryPublicationRefused(RuntimeError):
    """Recovery evidence is insufficient or live state changed."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as exc:
        raise RecoveryPublicationRefused(f"unreadable evidence: {path.name}") from exc
    if not isinstance(value, dict):
        raise RecoveryPublicationRefused(f"invalid evidence object: {path.name}")
    return value


def validate_evidence(evidence: dict[str, Any]) -> None:
    if evidence.get("schema_version") != 1:
        raise RecoveryPublicationRefused("unsupported recovery evidence schema")
    if evidence.get("owner_control_enabled") is not False:
        raise RecoveryPublicationRefused("owner control not released")
    if evidence.get("relevant_worker_processes") != []:
        raise RecoveryPublicationRefused("live worker process evidence present")
    pool = evidence.get("worker_pool")
    if not isinstance(pool, dict) or pool.get("running_total") != 0 or pool.get("runtime_leases") != []:
        raise RecoveryPublicationRefused("runtime worker or lease ownership remains")
    if evidence.get("worker_states_safe") is not True:
        raise RecoveryPublicationRefused("worker state reconciliation incomplete")
    refs = evidence.get("protected_refs")
    if not isinstance(refs, list) or any(not isinstance(row, dict) or row.get("verified") is not True for row in refs):
        raise RecoveryPublicationRefused("unpromoted work is not durably protected")
    proofs = evidence.get("historical_active_proofs")
    if not isinstance(proofs, dict):
        raise RecoveryPublicationRefused("historical active-grant proof missing")


def reconcile_ledger(
    preview: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    validate_evidence(evidence)
    recovered = deepcopy(preview)
    grants = recovered.get("grants")
    if not isinstance(grants, dict):
        raise RecoveryPublicationRefused("preview grants missing")

    post_snapshot_retired = 0
    historical_retired = 0
    proofs = evidence["historical_active_proofs"]

    for grant_id, record in grants.items():
        if not isinstance(record, dict):
            raise RecoveryPublicationRefused(f"invalid grant record: {grant_id}")
        if record.get("state") == "QUARANTINED" and record.get("result_status") == "LEDGER_LOSS_OUTCOME_UNVERIFIED":
            record["state"] = "ABANDONED"
            record["recovery_state"] = "RECOVERED"
            record["result_status"] = "LEDGER_LOSS_RECONCILED_NO_LIVE_OWNER"
            record["recovery_result"] = "RETIRED_AFTER_PROCESS_WORKTREE_AND_REF_RECONCILIATION"
            record["recovery_capa"] = "grant-ledger-nul-recovery-v1"
            post_snapshot_retired += 1
            continue
        if record.get("state") in {"ISSUED", "CLAIMED", "RUNNING"}:
            proof = proofs.get(grant_id)
            if not isinstance(proof, dict):
                raise RecoveryPublicationRefused(f"active historical grant lacks proof: {grant_id}")
            if proof.get("owner_alive") is not False:
                raise RecoveryPublicationRefused(f"historical owner not proven dead: {grant_id}")
            if proof.get("branch_head_equals_base") is not True:
                raise RecoveryPublicationRefused(f"historical branch contains unprotected work: {grant_id}")
            record["state"] = "ABANDONED"
            record["result_status"] = "LEDGER_LOSS_HISTORICAL_OWNER_DEAD_NO_COMMIT"
            record["recovery_state"] = "RECOVERED"
            record["recovery_capa"] = "grant-ledger-nul-recovery-v1"
            historical_retired += 1

    remaining = [
        grant_id for grant_id, record in grants.items()
        if isinstance(record, dict) and record.get("state") in OWNING_STATES
    ]
    if remaining:
        raise RecoveryPublicationRefused(
            "owning grants remain after reconciliation: " + ",".join(remaining[:8])
        )

    recovered["forensic_reconstruction"]["publication_method"] = "reconciled_then_lock_protected_atomic_publish"
    recovered["forensic_reconstruction"]["post_snapshot_retired"] = post_snapshot_retired
    recovered["forensic_reconstruction"]["historical_active_retired"] = historical_retired
    recovered["forensic_reconstruction"]["recovery_capa"] = "grant-ledger-nul-recovery-v1"
    return recovered, {
        "post_snapshot_retired": post_snapshot_retired,
        "historical_active_retired": historical_retired,
        "remaining_owning_grants": 0,
    }


def publish_reconciled_ledger(
    *,
    state_root: Path,
    snapshot: Path,
    evidence_path: Path,
    backup_path: Path,
    expected_live_sha256: str,
) -> dict[str, Any]:
    live = state_root / "hands-off-grant-lifecycle.json"
    issued = state_root / "hands-off-execution-grants"
    evidence = _load_json(evidence_path)
    preview, stats = preview_ledger(live, snapshot, issued)
    recovered, reconciliation = reconcile_ledger(preview, evidence)

    before = live.read_bytes()
    if _sha256(before) != expected_live_sha256:
        raise RecoveryPublicationRefused("live ledger hash does not match approved incident")
    if backup_path.exists():
        raise RecoveryPublicationRefused("corrupt-ledger backup already exists")
    if state_root.resolve() in backup_path.resolve().parents:
        raise RecoveryPublicationRefused("backup must be outside live state")

    lock = acquire_lifecycle_lock(live)
    try:
        locked = live.read_bytes()
        if _sha256(locked) != expected_live_sha256 or locked != before:
            raise RecoveryPublicationRefused("live ledger changed before publication")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with backup_path.open("xb") as out:
            out.write(locked)
            out.flush()
        atomic_lifecycle_json(live, recovered)
    finally:
        release_lifecycle_lock(lock)

    readback = _load_json(live)
    grants = readback.get("grants")
    if not isinstance(grants, dict) or len(grants) != len(recovered["grants"]):
        raise RecoveryPublicationRefused("published ledger readback failed")
    remaining = [
        grant_id for grant_id, record in grants.items()
        if isinstance(record, dict) and record.get("state") in OWNING_STATES
    ]
    if remaining:
        raise RecoveryPublicationRefused("published ledger still contains owning grants")

    return {
        "status": "RECOVERED_LEDGER_PUBLISHED",
        "records": len(grants),
        "backup_path": str(backup_path),
        "backup_sha256": _sha256(backup_path.read_bytes()),
        "published_sha256": _sha256(live.read_bytes()),
        "source_original_sha256": stats["original_sha256"],
        **reconciliation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a fully reconciled forensic grant ledger")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--backup-path", type=Path, required=True)
    parser.add_argument("--expected-live-sha256", required=True)
    args = parser.parse_args()

    marker = _load_json(args.state_root / "human-mode.json")
    if marker.get("enabled") is not False:
        raise RecoveryPublicationRefused("OWNER_CONTROL_UNVERIFIED")

    result = publish_reconciled_ledger(
        state_root=args.state_root,
        snapshot=args.snapshot,
        evidence_path=args.evidence,
        backup_path=args.backup_path,
        expected_live_sha256=args.expected_live_sha256,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
