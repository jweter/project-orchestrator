import json

import pytest

from project_orchestrator.ledger_forensics import (
    LedgerRecoveryRefused,
    preview_ledger,
)


def incident(tmp_path):
    state = tmp_path
    live = state / "hands-off-grant-lifecycle.json"
    live.write_bytes(bytes(4096))
    snapshot = state / "old-complete-snapshot.json"
    old_record = {"state": "COMPLETED", "result_status": "MERGED_VERIFIED"}
    snapshot.write_text(json.dumps({
        "schema_version": 1, "grants": {"old": old_record}
    }), encoding="utf-8")
    issued = state / "hands-off-execution-grants"
    issued.mkdir()
    (issued / "new.json").write_text(json.dumps({
        "grant_id": "new",
        "worker_id": "w1",
        "execution_slice": {
            "repository": "jweter/VoiceStudio",
            "runner_id": "claude-code", "exact_head": "a" * 40,
        },
        "products": {
            "voice": {"repositories": {"jweter/VoiceStudio": "a" * 40}},
        },
    }), encoding="utf-8")
    return live, snapshot, issued


def test_snapshot_preserved_and_every_unknown_grant_quarantined(tmp_path):
    live, snapshot, issued = incident(tmp_path)
    source = live.read_bytes()
    result, stats = preview_ledger(live, snapshot, issued)
    assert result["grants"]["old"] == {
        "state": "COMPLETED", "result_status": "MERGED_VERIFIED"
    }
    reconstructed = result["grants"]["new"]
    assert reconstructed["state"] == "QUARANTINED"
    assert reconstructed["recovery_state"] == "QUARANTINED"
    assert reconstructed["expected_sha"] == "a" * 40
    assert "finished_at" not in reconstructed
    assert stats["new_unverified_quarantines"] == 1
    assert stats["live_ledger_modified"] is False
    assert live.read_bytes() == source


def test_refuses_nonzero_live_file(tmp_path):
    live, snapshot, issued = incident(tmp_path)
    live.write_text('{"schema_version": 1, "grants": {}}')
    with pytest.raises(LedgerRecoveryRefused):
        preview_ledger(live, snapshot, issued)


def test_refuses_invalid_snapshot(tmp_path):
    live, snapshot, issued = incident(tmp_path)
    snapshot.write_text("not json")
    with pytest.raises(LedgerRecoveryRefused, match="snapshot is unreadable"):
        preview_ledger(live, snapshot, issued)


def test_refuses_fabricated_or_incorrect_grant_identity(tmp_path):
    live, snapshot, issued = incident(tmp_path)
    file = issued / "new.json"
    obj = json.loads(file.read_text())
    obj["grant_id"] = "another"
    file.write_text(json.dumps(obj))
    with pytest.raises(LedgerRecoveryRefused, match="identity mismatch"):
        preview_ledger(live, snapshot, issued)


def test_refuses_missing_exact_head_authority(tmp_path):
    live, snapshot, issued = incident(tmp_path)
    file = issued / "new.json"
    obj = json.loads(file.read_text())
    obj["products"]["voice"]["repositories"]["jweter/VoiceStudio"] = "main"
    file.write_text(json.dumps(obj))
    with pytest.raises(LedgerRecoveryRefused, match="invalid exact-head"):
        preview_ledger(live, snapshot, issued)


def test_multiple_new_grants_all_remain_unverified(tmp_path):
    live, snapshot, issued = incident(tmp_path)
    base = json.loads((issued / "new.json").read_text())
    for i in range(3):
        clone = {**base, "grant_id": f"another-{i}"}
        (issued / f"another-{i}.json").write_text(json.dumps(clone))
    result, stats = preview_ledger(live, snapshot, issued)
    assert stats["new_unverified_quarantines"] == 4
    assert stats["total_records"] == 5
    assert all(result["grants"][k]["state"] == "QUARANTINED"
               for k in result["grants"] if k != "old")


def test_preview_cli_writes_only_outside_live_state(tmp_path, monkeypatch):
    from project_orchestrator.ledger_forensics import main

    state = tmp_path / "state"
    state.mkdir()
    live, snapshot, issued = incident(state)
    preview = tmp_path / "forensics" / "quarantine.json"
    monkeypatch.setattr("sys.argv", [
        "ledger_forensics", "--state-root", str(state),
        "--snapshot", str(snapshot), "--preview-path", str(preview),
    ])
    assert main() == 0
    assert live.read_bytes() == bytes(4096)
    assert json.loads(preview.read_text())["grants"]["new"]["state"] == "QUARANTINED"
    with pytest.raises(LedgerRecoveryRefused, match="overwritten"):
        main()


def test_preview_cli_refuses_overwriting_live_state(tmp_path, monkeypatch):
    from project_orchestrator.ledger_forensics import main

    state = tmp_path / "state"
    state.mkdir()
    live, snapshot, issued = incident(state)
    monkeypatch.setattr("sys.argv", [
        "ledger_forensics", "--state-root", str(state),
        "--snapshot", str(snapshot), "--preview-path", str(live),
    ])
    with pytest.raises(LedgerRecoveryRefused, match="outside live state"):
        main()
    assert live.read_bytes() == bytes(4096)
