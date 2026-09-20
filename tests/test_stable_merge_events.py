import json
import scripts.continuous_flow_supervisor as flow

merge_watcher_advanced=flow.merge_watcher_advanced

def write_watch(root, timestamp, results):
    (root/"green-pr-watcher.json").write_text(json.dumps({
        "updated_at_utc":timestamp,"results":results
    }),encoding="utf-8")

def test_same_merge_is_consumed_once_across_watcher_polls(tmp_path,monkeypatch):\n    monkeypatch.setattr(flow,"reconcile_merge_feedback",lambda state,merged: [])
    event={"repository":"o/r","pr":7,"head":"abc","merged":True}
    write_watch(tmp_path,"2026-09-20T00:00:00Z",[event])
    assert merge_watcher_advanced(tmp_path) is True
    write_watch(tmp_path,"2026-09-20T00:01:00Z",[event])
    assert merge_watcher_advanced(tmp_path) is False

def test_new_exact_merge_identity_advances_again(tmp_path,monkeypatch):\n    monkeypatch.setattr(flow,"reconcile_merge_feedback",lambda state,merged: [])
    write_watch(tmp_path,"t1",[{"repository":"o/r","pr":7,"head":"abc","merged":True}])
    assert merge_watcher_advanced(tmp_path) is True
    write_watch(tmp_path,"t2",[{"repository":"o/r","pr":8,"head":"def","merged":True}])
    assert merge_watcher_advanced(tmp_path) is True

def test_result_order_does_not_create_false_merge_event(tmp_path,monkeypatch):\n    monkeypatch.setattr(flow,"reconcile_merge_feedback",lambda state,merged: [])
    a={"repository":"o/a","pr":1,"head":"a","merged":True}
    b={"repository":"o/b","pr":2,"head":"b","merged":True}
    write_watch(tmp_path,"t1",[a,b])
    assert merge_watcher_advanced(tmp_path) is True
    write_watch(tmp_path,"t2",[b,a])
    assert merge_watcher_advanced(tmp_path) is False
