import json
from scripts.continuous_flow_supervisor import reconcile_verified_merge

def lifecycle(root, records):
    (root/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":records}),encoding="utf-8")

def record():
    return {"repository":"o/r","pr_number":7,"head_sha":"abc","capability_id":"cap-1","slice_id":"slice-2",
            "execution_slice":{"acceptance":["tests pass"]}}

def test_exact_merge_emits_durable_slice_completion_evidence(tmp_path):
    lifecycle(tmp_path,{"g1":record()})
    result=reconcile_verified_merge(tmp_path,{"repository":"o/r","pr":7,"head":"abc"})
    assert result["status"]=="SLICE_VERIFIED_COMPLETE"
    evidence=json.loads((tmp_path/"slice-completion-evidence"/"o__r__slice-2.json").read_text())
    assert evidence["grant_id"]=="g1"
    assert evidence["capability_id"]=="cap-1"
    assert evidence["slice_id"]=="slice-2"
    assert evidence["head_sha"]=="abc"
    assert evidence["acceptance"]==["tests pass"]

def test_same_exact_merge_is_idempotent(tmp_path):
    lifecycle(tmp_path,{"g1":record()})
    event={"repository":"o/r","pr":7,"head":"abc"}
    assert reconcile_verified_merge(tmp_path,event)["status"]=="SLICE_VERIFIED_COMPLETE"
    assert reconcile_verified_merge(tmp_path,event)["status"]=="ALREADY_RECONCILED"

def test_merge_must_match_exact_pr_and_head(tmp_path):
    lifecycle(tmp_path,{"g1":record()})
    assert reconcile_verified_merge(tmp_path,{"repository":"o/r","pr":7,"head":"different"})["status"]=="MERGE_SLICE_IDENTITY_UNPROVEN"
    assert reconcile_verified_merge(tmp_path,{"repository":"o/r","pr":8,"head":"abc"})["status"]=="MERGE_SLICE_IDENTITY_UNPROVEN"

def test_ambiguous_lifecycle_identity_fails_closed(tmp_path):
    lifecycle(tmp_path,{"g1":record(),"g2":record()})
    result=reconcile_verified_merge(tmp_path,{"repository":"o/r","pr":7,"head":"abc"})
    assert result=={"status":"MERGE_SLICE_IDENTITY_UNPROVEN","matches":2}
