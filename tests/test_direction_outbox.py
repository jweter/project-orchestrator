import json
from scripts import continuous_flow_supervisor as supervisor

def test_direction_outbox_renders_specific_human_governance_message(tmp_path):
    root=tmp_path/"direction-requests"; root.mkdir()
    (root/"owner__repo.json").write_text(json.dumps({
        "status":"DIRECTION_REQUIRED","repository":"owner/repo","exact_head":"abc",
        "reason":"DIRECTION_REQUIRED","issues":[{"code":"EXECUTION_SLICE_REQUIRED","capability":"cap"}]}))
    messages=supervisor.direction_request_outbox(tmp_path)
    assert len(messages)==1
    assert messages[0]["kind"]=="PROJECT_DIRECTION_REQUIRED"
    assert messages[0]["repository"]=="owner/repo"
    assert messages[0]["exact_head"]=="abc"
    assert "EXECUTION_SLICE_REQUIRED" in messages[0]["message"]
    assert "do not invent requirements" in messages[0]["message"]

def test_direction_outbox_is_project_scoped_and_skips_resolved_requests(tmp_path):
    root=tmp_path/"direction-requests"; root.mkdir()
    (root/"a.json").write_text(json.dumps({"status":"READY","repository":"o/a"}))
    (root/"b.json").write_text(json.dumps({"status":"DIRECTION_REQUIRED","repository":"o/b","issues":[]}))
    assert [m["repository"] for m in supervisor.direction_request_outbox(tmp_path)]==["o/b"]

def test_publish_direction_outbox_clears_when_requests_are_resolved(tmp_path):
    root=tmp_path/"direction-requests"; root.mkdir()
    supervisor.publish_direction_outbox(tmp_path)
    payload=json.loads((tmp_path/"direction-outbox.json").read_text())
    assert payload["schema_version"]==1
    assert payload["messages"]==[]
