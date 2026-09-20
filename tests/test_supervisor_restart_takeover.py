import scripts.continuous_flow_supervisor as supervisor

def _lock(instance_id="new"):
    return {"worker":{"instance_id":instance_id,"host":"h","pid":2,"process_start_fingerprint":"p"}}

def test_restart_takeover_proof_links_predecessor_to_new_incarnation(tmp_path):
    burst={"burst_id":"b","resumed":True,"previous_process_instance_id":"old",
           "process_instance_id":"new"}
    proof=supervisor.prove_restart_takeover(tmp_path,_lock(),burst)
    assert proof["status"]=="RESTART_PROVEN_BY_EXCLUSIVE_KERNEL_LOCK"
    assert proof["previous_process_instance_id"]=="old"
    assert proof["process_instance_id"]=="new"
    assert proof["previous_owner_evidence"]=="KERNEL_LOCK_RELEASED_OR_PROCESS_EXITED"

def test_new_burst_requires_no_takeover(tmp_path):
    burst={"burst_id":"b","resumed":False,"previous_process_instance_id":None,
           "process_instance_id":"new"}
    proof=supervisor.prove_restart_takeover(tmp_path,_lock(),burst)
    assert proof["status"]=="NEW_BURST_NO_TAKEOVER"

def test_restart_identity_collision_fails_closed(tmp_path):
    burst={"burst_id":"b","resumed":True,"previous_process_instance_id":"same",
           "process_instance_id":"same"}
    proof=supervisor.prove_restart_takeover(tmp_path,_lock("same"),burst)
    assert proof["status"]=="RESTART_IDENTITY_COLLISION"

def test_legacy_resumed_burst_records_missing_predecessor_identity(tmp_path):
    burst={"burst_id":"b","resumed":True,"previous_process_instance_id":None,
           "process_instance_id":"new"}
    proof=supervisor.prove_restart_takeover(tmp_path,_lock(),burst)
    assert proof["status"]=="RESTART_PROVEN_BY_EXCLUSIVE_KERNEL_LOCK"
    assert proof["previous_owner_evidence"]=="LEGACY_IDENTITY_UNAVAILABLE"
