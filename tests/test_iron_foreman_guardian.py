from datetime import UTC, datetime, timedelta
import json

from scripts.iron_foreman_guardian import guardian_iteration, recovery_rate_allowed

def write(path,payload): path.write_text(json.dumps(payload),encoding="utf-8")

def test_guardian_watches_without_mutating_healthy_generation(tmp_path):
    now=datetime.now(UTC)
    write(tmp_path/"human-mode.json",{"enabled":False})
    write(tmp_path/"supervisor-process-instance.json",{"process_instance_id":"g","burst_id":"b",
        "worker":{"pid":1,"process_start_fingerprint":"x"}})
    write(tmp_path/"supervisor-generation-heartbeat.json",{"process_instance_id":"g","burst_id":"b",
        "status":"RUNNING_CURRENT","heartbeat_at_utc":now.isoformat()})
    write(tmp_path/"supervisor-progress.json",{"process_instance_id":"g","burst_id":"b","sequence":1,
        "observed_at_utc":now.isoformat()})
    result=guardian_iteration(tmp_path,guardian_id="guardian",confirmation_seconds=30)
    assert result["status"]=="NO_RECOVERY"
    assert not (tmp_path/"supervisor-generation-fence.json").exists()
    hb=json.loads((tmp_path/"iron-foreman-guardian.json").read_text())
    assert hb["status"]=="WATCHING"

def test_recovery_rate_limit_blocks_restart_storm(tmp_path):
    now=datetime.now(UTC)
    write(tmp_path/"iron-foreman-last-recovery.json",{"attempted_at_utc":now.isoformat()})
    assert not recovery_rate_allowed(tmp_path,now=now+timedelta(seconds=299),cooldown_seconds=300)
    assert recovery_rate_allowed(tmp_path,now=now+timedelta(seconds=300),cooldown_seconds=300)

def test_unreadable_recovery_receipt_fails_closed(tmp_path):
    (tmp_path/"iron-foreman-last-recovery.json").write_text('{"attempted_at_utc":"bad"}',encoding="utf-8")
    assert not recovery_rate_allowed(tmp_path,cooldown_seconds=300)
