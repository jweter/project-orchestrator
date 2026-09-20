import json
from scripts import autonomous_slice_builder as builder

def test_claim_persists_exact_slice_identity_for_merge_reconciliation(tmp_path,monkeypatch):
    grant=tmp_path/"hands-off-execution-grant.json"
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    grant.write_text(json.dumps({
        "grant_id":"g1",
        "execution_slice":{
            "capability_id":"cap-1","slice_id":"slice-2","repository":"o/r",
            "exact_head":"abc","objective":"do bounded work","acceptance":["tests pass"]
        }
    }))
    monkeypatch.setattr(builder,"worker_incarnation",lambda:{"host":"h","pid":1})
    assert builder.claim_execution_grant(grant,"o/r","abc",lifecycle_path=lifecycle)
    record=json.loads(lifecycle.read_text())["grants"]["g1"]
    assert record["capability_id"]=="cap-1"
    assert record["slice_id"]=="slice-2"
    assert record["execution_slice"]["repository"]=="o/r"
    assert record["execution_slice"]["exact_head"]=="abc"
    assert record["execution_slice"]["acceptance"]==["tests pass"]

def test_claim_fails_closed_without_exact_slice_authority(tmp_path):
    grant=tmp_path/"hands-off-execution-grant.json"
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    grant.write_text(json.dumps({"grant_id":"g1"}))
    assert not builder.claim_execution_grant(grant,"o/r","abc",lifecycle_path=lifecycle)
    assert not lifecycle.exists()
