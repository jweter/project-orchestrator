import json, threading, time
from datetime import UTC, datetime, timedelta
import scripts.continuous_flow_supervisor as supervisor

def test_stale_issue_recovery_cannot_overwrite_concurrent_claim(tmp_path):
    state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    old=(datetime.now(UTC)-timedelta(seconds=supervisor.ISSUE_LEASE_SECONDS+10)).isoformat()
    path.write_text(json.dumps({"schema_version":1,"grants":{"g":{"state":"ISSUED","issued_at":old,"issuer":{"host":"x"}}}}))
    entered=threading.Event(); release=threading.Event()
    def probe(_):
        entered.set(); release.wait(2); return "DEAD"
    original=supervisor.worker_liveness
    supervisor.worker_liveness=probe
    try:
        t=threading.Thread(target=supervisor.recover_stale_issued_grants,args=(state,))
        t.start(); assert entered.wait(1)
        acquired=[]
        def claimant():
            h=supervisor._acquire_lifecycle_lock(path); acquired.append(True); supervisor._release_lifecycle_lock(h)
        c=threading.Thread(target=claimant); c.start()
        time.sleep(.05); assert acquired==[]
        release.set(); t.join(); c.join()
        assert acquired==[True]
        assert json.loads(path.read_text())["grants"]["g"]["state"]=="ABANDONED"
    finally:
        supervisor.worker_liveness=original
