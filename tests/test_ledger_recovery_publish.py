import hashlib
import json

import pytest

from project_orchestrator.ledger_forensics import preview_ledger
from project_orchestrator.ledger_recovery_publish import (
    RecoveryPublicationRefused,
    publish_reconciled_ledger,
    reconcile_ledger,
)
from tests.owner_authority import grant_owner_authority


def issue(grant_id, repo="o/r", head="a" * 40):
    return {
        "schema_version": 5,
        "grant_id": grant_id,
        "worker_id": "worker-1",
        "execution_slice": {
            "repository": repo,
            "slice_id": "slice-v1",
            "exact_head": head,
            "runner_id": "claude-code",
            "execution_runtime": "native-windows",
        },
        "products": {"p": {"repositories": {repo: head}}},
    }


def incident(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    grant_owner_authority(state)
    live = state / "hands-off-grant-lifecycle.json"
    live.write_bytes(bytes(4096))
    snapshot = tmp_path / "snapshot.json"
    historical = {
        "schema_version": 1,
        "grants": {
            "old-active": {
                "state": "CLAIMED",
                "repository": "o/r",
                "base_sha": "a" * 40,
                "branch": "actor/old",
                "execution_slice": {
                    "repository": "o/r",
                    "slice_id": "slice-v1",
                    "exact_head": "a" * 40,
                },
            },
            "old-done": {"state": "COMPLETED", "result_status": "MERGED"},
        },
    }
    snapshot.write_text(json.dumps(historical), encoding="utf-8")
    issued = state / "hands-off-execution-grants"
    issued.mkdir()
    (issued / "old-active.json").write_text(
        json.dumps(issue("old-active")), encoding="utf-8"
    )
    (issued / "new-grant.json").write_text(
        json.dumps(issue("new-grant")), encoding="utf-8"
    )
    return state, live, snapshot


def evidence():
    return {
        "schema_version": 1,
        "owner_control_enabled": False,
        "relevant_worker_processes": [],
        "worker_pool": {"running_total": 0, "runtime_leases": []},
        "worker_states_safe": True,
        "protected_refs": [],
        "historical_active_proofs": {
            "old-active": {
                "owner_alive": False,
                "branch_head_equals_base": True,
            }
        },
        "post_snapshot_quarantine_proofs": {
            "new-grant": {
                "owner_alive": False,
                "no_product_work": True,
            }
        },
    }


def test_reconcile_retires_unknown_and_dead_historical_owner(tmp_path):
    state, live, snapshot = incident(tmp_path)
    preview, _ = preview_ledger(
        live, snapshot, state / "hands-off-execution-grants"
    )
    recovered, stats = reconcile_ledger(preview, evidence())
    assert recovered["grants"]["new-grant"]["state"] == "ABANDONED"
    assert recovered["grants"]["old-active"]["state"] == "ABANDONED"
    assert recovered["grants"]["old-done"]["state"] == "COMPLETED"
    assert stats == {
        "post_snapshot_retired": 1,
        "historical_active_retired": 1,
        "remaining_owning_grants": 0,
    }


def test_reconcile_refuses_active_historical_without_proof(tmp_path):
    state, live, snapshot = incident(tmp_path)
    preview, _ = preview_ledger(
        live, snapshot, state / "hands-off-execution-grants"
    )
    bad = evidence()
    bad["historical_active_proofs"] = {}
    with pytest.raises(RecoveryPublicationRefused, match="lacks proof"):
        reconcile_ledger(preview, bad)


def test_reconcile_refuses_live_workers(tmp_path):
    state, live, snapshot = incident(tmp_path)
    preview, _ = preview_ledger(
        live, snapshot, state / "hands-off-execution-grants"
    )
    bad = evidence()
    bad["relevant_worker_processes"] = [{"pid": 7}]
    with pytest.raises(RecoveryPublicationRefused, match="live worker"):
        reconcile_ledger(preview, bad)


def test_publish_preserves_corrupt_bytes_and_writes_readable_ledger(tmp_path):
    state, live, snapshot = incident(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence()), encoding="utf-8")
    backup = tmp_path / "forensics" / "corrupt.bin"
    before = live.read_bytes()
    result = publish_reconciled_ledger(
        state_root=state,
        snapshot=snapshot,
        evidence_path=evidence_path,
        backup_path=backup,
        expected_live_sha256=hashlib.sha256(before).hexdigest(),
    )
    assert result["status"] == "RECOVERED_LEDGER_PUBLISHED"
    assert backup.read_bytes() == before
    published = json.loads(live.read_text())
    assert len(published["grants"]) == 3
    assert published["grants"]["new-grant"]["state"] == "ABANDONED"
    assert published["grants"]["old-active"]["state"] == "ABANDONED"


def test_publish_refuses_wrong_incident_hash(tmp_path):
    state, live, snapshot = incident(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence()), encoding="utf-8")
    with pytest.raises(RecoveryPublicationRefused, match="hash"):
        publish_reconciled_ledger(
            state_root=state,
            snapshot=snapshot,
            evidence_path=evidence_path,
            backup_path=tmp_path / "corrupt.bin",
            expected_live_sha256="0" * 64,
        )
