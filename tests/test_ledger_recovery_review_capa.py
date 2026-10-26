import hashlib
import json
import shutil

import pytest

from project_orchestrator.ledger_forensics import preview_ledger
from project_orchestrator.ledger_recovery_publish import (
    RecoveryPublicationRefused,
    publish_reconciled_ledger,
    reconcile_ledger,
)
from project_orchestrator.iron_god_owner_control import control_root
from test_ledger_recovery_publish import evidence, incident
from tests.owner_authority import revoke_owner_authority, tamper_owner_authority


def test_post_snapshot_grant_requires_specific_no_work_proof(tmp_path):
    state, live, snapshot = incident(tmp_path)
    preview, _ = preview_ledger(live, snapshot, state / "hands-off-execution-grants")
    data = evidence()
    data["post_snapshot_quarantine_proofs"] = {}
    with pytest.raises(RecoveryPublicationRefused, match="lacks proof"):
        reconcile_ledger(preview, data)


def test_post_snapshot_grant_with_product_work_stays_fail_closed(tmp_path):
    state, live, snapshot = incident(tmp_path)
    preview, _ = preview_ledger(live, snapshot, state / "hands-off-execution-grants")
    data = evidence()
    data["post_snapshot_quarantine_proofs"]["new-grant"]["no_product_work"] = False
    with pytest.raises(RecoveryPublicationRefused, match="unresolved product work"):
        reconcile_ledger(preview, data)


def test_owner_change_blocks_publication(tmp_path):
    state, live, snapshot = incident(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence()), encoding="utf-8")
    before = live.read_bytes()
    revoke_owner_authority(state)
    with pytest.raises(RecoveryPublicationRefused, match="owner control changed"):
        publish_reconciled_ledger(
            state_root=state,
            snapshot=snapshot,
            evidence_path=evidence_path,
            backup_path=tmp_path / "forensics" / "corrupt.bin",
            expected_live_sha256=hashlib.sha256(before).hexdigest(),
        )
    assert live.read_bytes() == before


def test_missing_owner_controller_state_refuses_publication(tmp_path):
    state, live, snapshot = incident(tmp_path)
    shutil.rmtree(control_root(state))
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence()), encoding="utf-8")
    before = live.read_bytes()
    with pytest.raises(RecoveryPublicationRefused, match="owner control changed"):
        publish_reconciled_ledger(
            state_root=state,
            snapshot=snapshot,
            evidence_path=evidence_path,
            backup_path=tmp_path / "forensics" / "corrupt.bin",
            expected_live_sha256=hashlib.sha256(before).hexdigest(),
        )
    assert live.read_bytes() == before


def test_tampered_owner_controller_state_refuses_publication(tmp_path):
    state, live, snapshot = incident(tmp_path)
    tamper_owner_authority(state)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence()), encoding="utf-8")
    before = live.read_bytes()
    with pytest.raises(RecoveryPublicationRefused, match="owner control changed"):
        publish_reconciled_ledger(
            state_root=state,
            snapshot=snapshot,
            evidence_path=evidence_path,
            backup_path=tmp_path / "forensics" / "corrupt.bin",
            expected_live_sha256=hashlib.sha256(before).hexdigest(),
        )
    assert live.read_bytes() == before
