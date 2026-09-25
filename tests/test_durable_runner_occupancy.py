import json
from datetime import UTC,datetime,timedelta
import scripts.continuous_flow_supervisor as supervisor

def test_durable_runner_occupancy_counts_only_live_grants(tmp_path):
    now=datetime.now(UTC).isoformat()
    payload={"grants":{
      "a":{"state":"ISSUED","runner_id":"claude-code"},
      "b":{"state":"CLAIMED","execution_slice":{"runner_id":"aider-ollama"},"claimed_at":now},
      "c":{"state":"RUNNING","runner_id":"claude-code","claimed_at":now},
      "d":{"state":"COMPLETED","runner_id":"claude-code"},
      "e":{"state":"FAILED","runner_id":"aider-ollama"},
    }}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps(payload),encoding="utf-8")
    assert supervisor.durable_runner_occupancy(tmp_path)=={"claude-code":2,"aider-ollama":1}

def test_durable_runner_occupancy_survives_missing_state(tmp_path):
    assert supervisor.durable_runner_occupancy(tmp_path)=={}

def test_durable_runner_occupancy_releases_proven_dead_stale_claim(tmp_path,monkeypatch):
    now=datetime(2026,9,25,23,30,tzinfo=UTC)
    stale=(now-timedelta(seconds=supervisor.CLAIM_LEASE_SECONDS+1)).isoformat()
    payload={"grants":{"a":{"state":"CLAIMED","runner_id":"claude-code","claimed_at":stale,"last_heartbeat_at":stale,"worker":{"pid":999}}}}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps(payload),encoding="utf-8")
    monkeypatch.setattr(supervisor,"worker_liveness",lambda worker:"DEAD")
    assert supervisor.durable_runner_occupancy(tmp_path,now)=={}

def test_durable_runner_occupancy_keeps_unknown_stale_claim_fail_closed(tmp_path,monkeypatch):
    now=datetime(2026,9,25,23,30,tzinfo=UTC)
    stale=(now-timedelta(seconds=supervisor.CLAIM_LEASE_SECONDS+1)).isoformat()
    payload={"grants":{"a":{"state":"CLAIMED","runner_id":"claude-code","claimed_at":stale,"last_heartbeat_at":stale,"worker":{"pid":999}}}}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps(payload),encoding="utf-8")
    monkeypatch.setattr(supervisor,"worker_liveness",lambda worker:"UNKNOWN")
    assert supervisor.durable_runner_occupancy(tmp_path,now)=={"claude-code":1}
