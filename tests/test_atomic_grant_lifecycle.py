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
    assert builder.advance_grant_stage(grant,"EXECUTING",head_sha="abc")
    state=json.loads(lifecycle.read_text())
    assert state["grants"]["a"]["execution_stage"]=="EXECUTING"
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
