from project_orchestrator.execution_continuation import learn_and_continue

SHA="a"*40
def result(status="PASS"):
 return {"project":"knowledge-engine","repository":"jweter/knowledge-engine-core","head_sha":SHA,"probe_id":"p1","pattern_id":"pat-1","evidence_ref":"ci:1","observed_at":"2026-09-19T00:00:00Z","status":status,"summary":"verified"}
def memory(): return {"schema_version":1,"entries":[]}
def cp(): return {"schema_version":1,"repositories":{"jweter/knowledge-engine-core":{"safe_repairs":[]}}}

def test_pass_releases_lane_and_dispatches_highest_priority(tmp_path):
 tasks=[{"repository":"jweter/knowledge-engine-core","task_key":"later","kind":"implementation","priority":5},{"repository":"jweter/knowledge-engine-core","task_key":"next","kind":"implementation","priority":2}]
 out=learn_and_continue(worker_result=result(),control_plane=cp(),memory=memory(),candidate_tasks=tasks,claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q")
 assert out["status"]=="CONTINUED"; assert out["selected_task"]["task_key"]=="next"
 assert out["dispatch"]["actor"]=="LAPTOP_ACTOR"; assert (tmp_path/"q"/"next.json").exists()

def test_failure_holds_lane_for_repair(tmp_path):
 out=learn_and_continue(worker_result=result("FAIL"),control_plane=cp(),memory=memory(),candidate_tasks=[{"repository":"jweter/knowledge-engine-core","task_key":"next","priority":1}],claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q")
 assert out["status"]=="LANE_HELD"; assert out["dispatch"] is None

def test_blocked_owned_and_other_repo_are_not_dispatched(tmp_path):
 tasks=[{"repository":"jweter/knowledge-engine-core","task_key":"blocked","priority":1,"blocked":True},{"repository":"jweter/knowledge-engine-core","task_key":"owned","priority":2,"owned":True},{"repository":"jweter/knowledge-engine-ai","task_key":"other","priority":1}]
 out=learn_and_continue(worker_result=result(),control_plane=cp(),memory=memory(),candidate_tasks=tasks,claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q")
 assert out["status"]=="NO_ELIGIBLE_WORK"; assert out["dispatch"] is None
