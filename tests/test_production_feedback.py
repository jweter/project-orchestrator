import json
from project_orchestrator.production_feedback import ingest_production_results
def write_result(p,status="PASS"):
 p.mkdir(parents=True);(p/"job1.result.json").write_text(json.dumps({"job_id":"job1","repository":"jweter/Project-Everward","status":status,"verification":[],"evidence_ref":"local://job1"}))
def test_pass_is_processed_once_and_releases_lane(tmp_path):
 results=tmp_path/"results";write_result(results)
 ctx={"jweter/Project-Everward":{"control_plane":{},"memory":{"schema_version":1,"entries":[]},"candidate_tasks":[]}}
 a=ingest_production_results(pending_dir=tmp_path/"p",results_dir=results,processed_dir=tmp_path/"done",lane_context=ctx,claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q")
 b=ingest_production_results(pending_dir=tmp_path/"p",results_dir=results,processed_dir=tmp_path/"done",lane_context=ctx,claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q")
 assert len(a)==1;assert a[0]["outcome"]["status"]=="NO_ELIGIBLE_WORK";assert b==[]
def test_failure_holds_lane(tmp_path):
 results=tmp_path/"results";write_result(results,"FAIL")
 ctx={"jweter/Project-Everward":{"control_plane":{},"memory":{"schema_version":1,"entries":[]},"candidate_tasks":[]}}
 out=ingest_production_results(pending_dir=tmp_path/"p",results_dir=results,processed_dir=tmp_path/"done",lane_context=ctx,claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q")
 assert out[0]["outcome"]["status"]=="LANE_HELD"
def test_unmapped_result_is_retained_not_lost(tmp_path):
 results=tmp_path/"results";write_result(results)
 out=ingest_production_results(pending_dir=tmp_path/"p",results_dir=results,processed_dir=tmp_path/"done",lane_context={},claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q")
 assert out[0]["outcome"]["status"]=="UNMAPPED_RESULT"
