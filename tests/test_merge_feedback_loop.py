import json
import scripts.continuous_flow_supervisor as flow

def write_watch(root,event):
    (root/"green-pr-watcher.json").write_text(json.dumps({"updated_at_utc":"t","results":[event]}))

def test_merge_reconciles_slice_and_capability_before_advancing_cursor(tmp_path,monkeypatch):
    write_watch(tmp_path,{"repository":"o/r","pr":7,"head":"abc","merged":True})
    calls=[]
    monkeypatch.setattr(flow,"reconcile_verified_merge",lambda root,event:
        calls.append(("slice",event["head"])) or {"status":"SLICE_VERIFIED_COMPLETE","capability_id":"cap","slice_id":"s"})
    monkeypatch.setattr(flow,"reconcile_capability_from_slice_evidence",lambda root,repo,cid:
        calls.append(("capability",cid)) or {"status":"CAPABILITY_VERIFIED_COMPLETE","capability_id":cid})
    assert flow.merge_watcher_advanced(tmp_path) is True
    assert calls==[("slice","abc"),("capability","cap")]
    assert (tmp_path/"green-pr-watcher-consumed.json").exists()
    assert (tmp_path/"merge-feedback-last.json").exists()

def test_unproven_merge_identity_blocks_cursor_and_next_work(tmp_path,monkeypatch):
    write_watch(tmp_path,{"repository":"o/r","pr":7,"head":"abc","merged":True})
    monkeypatch.setattr(flow,"reconcile_verified_merge",lambda root,event:
        {"status":"MERGE_SLICE_IDENTITY_UNPROVEN","matches":0})
    assert flow.merge_watcher_advanced(tmp_path) is False
    assert not (tmp_path/"green-pr-watcher-consumed.json").exists()
    assert (tmp_path/"merge-feedback-blocked.json").exists()

def test_partial_capability_progress_still_allows_next_slice_selection(tmp_path,monkeypatch):
    write_watch(tmp_path,{"repository":"o/r","pr":7,"head":"abc","merged":True})
    monkeypatch.setattr(flow,"reconcile_verified_merge",lambda root,event:
        {"status":"SLICE_VERIFIED_COMPLETE","capability_id":"cap","slice_id":"s1"})
    monkeypatch.setattr(flow,"reconcile_capability_from_slice_evidence",lambda root,repo,cid:
        {"status":"CAPABILITY_IN_PROGRESS","capability_id":cid})
    assert flow.merge_watcher_advanced(tmp_path) is True
