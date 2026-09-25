import json
import scripts.continuous_flow_supervisor as supervisor

def test_ownership_held_is_not_reported_healthy(tmp_path):
    (tmp_path/"builder.json").write_text(json.dumps({"status":"OWNERSHIP_HELD"}),encoding="utf-8")
    assert supervisor.builder_health(tmp_path,0)==(False,"OWNERSHIP_HELD")

def test_no_work_is_not_reported_healthy(tmp_path):
    (tmp_path/"builder.json").write_text(json.dumps({"status":"NO_WORK"}),encoding="utf-8")
    assert supervisor.builder_health(tmp_path,0)==(False,"NO_WORK")

def test_productive_builder_state_remains_healthy(tmp_path):
    (tmp_path/"builder.json").write_text(json.dumps({"status":"PREFLIGHT_GREEN"}),encoding="utf-8")
    assert supervisor.builder_health(tmp_path,0)==(True,"PREFLIGHT_GREEN")
