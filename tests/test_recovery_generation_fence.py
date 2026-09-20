import json
from pathlib import Path
import scripts.continuous_flow_supervisor as supervisor

def _write(path, record):
    path.write_text(json.dumps({"schema_version":1,"grants":{"g":record}}))

def test_recovery_generation_is_single_writer_and_monotonic(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    original={"state":"ABANDONED","recovery_state":"RECOVERING","authorized_repositories":["o/repo"]}
    _write(path,original)
    first=supervisor.begin_recovery_generation(path,"g",dict(original))
    assert first is not None
    token,reserved=first
    assert reserved["recovery_generation"]==token
    assert reserved["recovery_version"]==1
    assert supervisor.begin_recovery_generation(path,"g",dict(original)) is None
    assert json.loads(path.read_text())["grants"]["g"]["recovery_generation"]==token

def test_complete_record_compare_rejects_retry_accounting_loss(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    original={"state":"ABANDONED","recovery_state":"RECOVERING","recovery_attempts":1,
              "authorized_repositories":["o/repo"]}
    _write(path,original)
    token,reserved=supervisor.begin_recovery_generation(path,"g",dict(original))
    live=json.loads(path.read_text())
    live["grants"]["g"]["recovery_attempts"]=2
    supervisor.atomic(path,live)
    updated=dict(reserved); updated["recovery_result"]="RECOVERED"
    assert supervisor.commit_recovery_record(path,"g",reserved,updated) is False
    assert json.loads(path.read_text())["grants"]["g"]["recovery_attempts"]==2

def test_active_recovery_generation_blocks_new_grant_conflict(tmp_path):
    state=tmp_path
    path=state/"hands-off-grant-lifecycle.json"
    record={"state":"ABANDONED","recovery_generation":"tok",
            "authorized_repositories":["o/repo"],
            "execution_slice":{"capability_id":"c","slice_id":"s","repository":"o/repo","exact_head":"abc"}}
    _write(path,record)
    conflict=supervisor.conflicting_nonterminal_grant(
        state,"o/repo",{"capability_id":"new","slice_id":"new","repository":"o/repo","exact_head":"def"})
    assert conflict is not None
    assert conflict["grant_id"]=="g"

def test_quarantine_recheck_does_not_reclaim_during_recovery_generation(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    record={"state":"QUARANTINED","result_status":"CLAIM_LEASE_UNKNOWN_QUARANTINED",
            "recovery_generation":"tok","worker":{"pid":1}}
    _write(path,record)
    called=[]
    out=supervisor.recheck_quarantined_claims(tmp_path,liveness_probe=lambda w: called.append(w) or "ALIVE")
    assert out["g"]=="RECOVERY_GENERATION_ACTIVE"
    assert called==[]
    assert json.loads(path.read_text())["grants"]["g"]["state"]=="QUARANTINED"
