import json
import scripts.continuous_flow_supervisor as supervisor

def _graph():
    return {"capabilities":[{"id":"C1","capability":"work","state":"PLANNED","depends_on":[],
        "completion_evidence":["green"],"slices":[{"id":"S1","state":"PLANNED","objective":"work","acceptance":["green"]}]}],
        "roadmap":["C1"]}

def _activation():
    return {"products":{"p":{"repositories":{"jweter/a":"a","jweter/b":"b"}}}}

def _graphs(state):
    graphs=state/"capability-graphs"; graphs.mkdir()
    for name in ("a","b"):
        (graphs/f"jweter__{name}.json").write_text(json.dumps(_graph()))

def test_ownership_quarantine_reserves_only_affected_repository(tmp_path):
    state=tmp_path; _graphs(state)
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"schema_version":1,"grants":{
        "g":{"state":"QUARANTINED","repository":"jweter/a","result_status":"CLAIM_LEASE_UNKNOWN_QUARANTINED"}}}))
    assert supervisor.select_execution_target(state,_activation(),["p"])==("jweter/b","b")

def test_preserved_work_quarantine_reserves_only_affected_repository(tmp_path):
    state=tmp_path; _graphs(state)
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"schema_version":1,"grants":{
        "g":{"state":"ABANDONED","recovery_state":"QUARANTINED","repository":"jweter/a",
             "recovery_quarantine_reason":"UNCOMMITTED_WORK_REQUIRES_PRESERVATION"}}}))
    assert supervisor.select_execution_target(state,_activation(),["p"])==("jweter/b","b")

def test_reservation_releases_after_quarantine_resolves(tmp_path):
    state=tmp_path; _graphs(state)
    lifecycle=state/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{
        "g":{"state":"ABANDONED","recovery_state":"QUARANTINED","repository":"jweter/a"}}}))
    assert supervisor.reserved_quarantine_repositories(state)=={"jweter/a"}
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{
        "g":{"state":"ABANDONED","recovery_state":"RECOVERED","repository":"jweter/a"}}}))
    assert supervisor.reserved_quarantine_repositories(state)==set()
    assert supervisor.select_execution_target(state,_activation(),["p"])==("jweter/a","a")
