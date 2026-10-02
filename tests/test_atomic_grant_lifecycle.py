import json
import threading
import scripts.autonomous_slice_builder as builder
import project_orchestrator.grant_lifecycle as lifecycle

def test_concurrent_grant_mutations_preserve_both_records(tmp_path):
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{
        "a":{"state":"CLAIMED"},"b":{"state":"CLAIMED"}}}),encoding="utf-8")
    barrier=threading.Barrier(2)
    results=[]
    def mutate(grant_id,value):
        barrier.wait()
        results.append(builder.mutate_grant_lifecycle(
            lifecycle,grant_id,lambda record:(record.__setitem__("marker",value) or True)))
    t1=threading.Thread(target=mutate,args=("a","A"))
    t2=threading.Thread(target=mutate,args=("b","B"))
    t1.start(); t2.start(); t1.join(); t2.join()
    state=json.loads(lifecycle.read_text())
    assert all(results)
    assert state["grants"]["a"]["marker"]=="A"
    assert state["grants"]["b"]["marker"]=="B"

def test_stage_update_preserves_other_grant(tmp_path):
    grants=tmp_path/"hands-off-execution-grants"; grants.mkdir()
    grant=grants/"a.json"; grant.write_text(json.dumps({"grant_id":"a"}),encoding="utf-8")
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{
        "a":{"state":"CLAIMED"},"b":{"state":"CLAIMED","marker":"keep"}}}),encoding="utf-8")
    assert builder.advance_grant_stage(grant,"BUILDING",head_sha="abc")
    state=json.loads(lifecycle.read_text())
    assert state["grants"]["a"]["execution_stage"]=="BUILDING"
    assert state["grants"]["b"]["marker"]=="keep"

def test_concurrent_first_lock_initialization_is_atomic(tmp_path):
    lock_path=tmp_path/"hands-off-grant-lifecycle.json.lock"
    barrier=threading.Barrier(8)
    errors=[]
    def initialize():
        barrier.wait()
        try:
            lifecycle.ensure_lifecycle_lock_file(lock_path)
        except Exception as exc:
            errors.append(exc)
    threads=[threading.Thread(target=initialize) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors==[]
    assert lock_path.read_bytes()==b"0"


def test_lifecycle_mutation_uses_extended_replace_window(tmp_path, monkeypatch):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"schema_version":1,"grants":{"a":{"state":"CLAIMED"}}}),encoding="utf-8")
    attempts=0
    real_replace=type(path).replace
    def transient_replace(self,target):
        nonlocal attempts
        if self.name.startswith("hands-off-grant-lifecycle.json.tmp."):
            attempts+=1
            if attempts<8:
                raise PermissionError("transient destination busy")
        return real_replace(self,target)
    monkeypatch.setattr(type(path),"replace",transient_replace)
    monkeypatch.setattr("project_orchestrator.state_io.time.sleep",lambda _seconds:None)
    assert lifecycle.mutate_grant_lifecycle(path,"a",lambda record:(record.__setitem__("marker","ok") or True))
    assert attempts==8
    assert json.loads(path.read_text())["grants"]["a"]["marker"]=="ok"
