import json
import scripts.continuous_flow_supervisor as supervisor

def test_durable_runner_occupancy_counts_only_live_grants(tmp_path):
    payload={"grants":{
      "a":{"state":"ISSUED","runner_id":"claude-code"},
      "b":{"state":"CLAIMED","execution_slice":{"runner_id":"aider-ollama"}},
      "c":{"state":"RUNNING","runner_id":"claude-code"},
      "d":{"state":"COMPLETED","runner_id":"claude-code"},
      "e":{"state":"FAILED","runner_id":"aider-ollama"},
    }}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps(payload),encoding="utf-8")
    assert supervisor.durable_runner_occupancy(tmp_path)=={"claude-code":2,"aider-ollama":1}

def test_durable_runner_occupancy_survives_missing_state(tmp_path):
    assert supervisor.durable_runner_occupancy(tmp_path)=={}
