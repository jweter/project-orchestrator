from datetime import UTC, datetime, timedelta
import json

from project_orchestrator.iron_foreman import prepare_reclamation, recovery_decision

NOW=datetime(2026,10,3,2,0,tzinfo=UTC)

def write(path,payload):
    path.write_text(json.dumps(payload),encoding="utf-8")

def stalled_state(root):
    write(root/"supervisor-process-instance.json",{"process_instance_id":"gen","burst_id":"burst",
        "worker":{"pid":42724,"process_start_fingerprint":"windows:123"}})
    write(root/"supervisor-generation-heartbeat.json",{"process_instance_id":"gen","burst_id":"burst",
        "status":"RUNNING_CURRENT","heartbeat_at_utc":(NOW-timedelta(seconds=121)).isoformat()})
    write(root/"supervisor-progress.json",{"process_instance_id":"gen","burst_id":"burst","sequence":4,
        "observed_at_utc":(NOW-timedelta(seconds=601)).isoformat()})

def test_stall_requires_bounded_confirmation(tmp_path):
    stalled_state(tmp_path)
    first=recovery_decision(tmp_path,now=NOW,confirmation_seconds=30)
    assert first["status"]=="STALL_CONFIRMING"
    second=recovery_decision(tmp_path,now=NOW+timedelta(seconds=29),confirmation_seconds=30)
    assert second["status"]=="STALL_CONFIRMING"
    third=recovery_decision(tmp_path,now=NOW+timedelta(seconds=30),confirmation_seconds=30)
    assert third["status"]=="STALL_CONFIRMED"

def test_generation_change_resets_confirmation(tmp_path):
    stalled_state(tmp_path)
    recovery_decision(tmp_path,now=NOW,confirmation_seconds=30)
    proc=json.loads((tmp_path/"supervisor-process-instance.json").read_text())
    proc["process_instance_id"]="new"; write(tmp_path/"supervisor-process-instance.json",proc)
    hb=json.loads((tmp_path/"supervisor-generation-heartbeat.json").read_text())
    hb["process_instance_id"]="new"; write(tmp_path/"supervisor-generation-heartbeat.json",hb)
    pg=json.loads((tmp_path/"supervisor-progress.json").read_text())
    pg["process_instance_id"]="new"; write(tmp_path/"supervisor-progress.json",pg)
    result=recovery_decision(tmp_path,now=NOW+timedelta(seconds=31),confirmation_seconds=30)
    assert result["status"]=="STALL_CONFIRMING"
    assert result["process_instance_id"]=="new"

def test_prepare_reclamation_rechecks_identity_then_fences(tmp_path):
    stalled_state(tmp_path)
    recovery_decision(tmp_path,now=NOW,confirmation_seconds=30)
    recovery_decision(tmp_path,now=NOW+timedelta(seconds=30),confirmation_seconds=30)
    result=prepare_reclamation(tmp_path,fingerprint_fn=lambda pid:"windows:123",
                               now=NOW+timedelta(seconds=31))
    assert result["status"]=="RECLAMATION_PREPARED"
    assert result["fence"]["process_instance_id"]=="gen"

def test_human_mode_blocks_recovery_and_reclamation(tmp_path):
    stalled_state(tmp_path)
    (tmp_path/"human-mode.json").write_text("{}",encoding="utf-8")
    decision=recovery_decision(tmp_path,now=NOW,confirmation_seconds=0)
    assert decision["status"]=="HUMAN_OWNED"
    result=prepare_reclamation(tmp_path,fingerprint_fn=lambda pid:"windows:123",now=NOW)
    assert result=={"status":"HUMAN_OWNED","prepared":False}
    assert not (tmp_path/"supervisor-generation-fence.json").exists()


def test_prepare_reclamation_rejects_process_identity_change(tmp_path):
    stalled_state(tmp_path)
    recovery_decision(tmp_path,now=NOW,confirmation_seconds=30)
    recovery_decision(tmp_path,now=NOW+timedelta(seconds=30),confirmation_seconds=30)
    result=prepare_reclamation(tmp_path,fingerprint_fn=lambda pid:"windows:999",
                               now=NOW+timedelta(seconds=31))
    assert result["status"]=="PROCESS_IDENTITY_CHANGED"
    assert not result["prepared"]
    assert not (tmp_path/"supervisor-generation-fence.json").exists()
