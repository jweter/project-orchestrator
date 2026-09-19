import json,time
from project_orchestrator.claude_dispatch import seed_external_dispatch
from project_orchestrator.execution_reconciliation import reconcile_laptop_queue,reconcile_claude_result,reconciliation_snapshot

def test_laptop_result_reconciles_terminal_evidence(tmp_path):
    q,r=tmp_path/"q",tmp_path/"r"; q.mkdir(); r.mkdir()
    (q/"x.json").write_text(json.dumps({"task_key":"x","repository":"jweter/x"}))
    (r/"x.json").write_text(json.dumps({"status":"PASS","verification":"green","commit_sha":"abc"}))
    out=reconcile_laptop_queue(q,r)
    assert out[0]["status"]=="PASS"
    assert out[0]["evidence"]["commit_sha"]=="abc"

def test_missing_laptop_result_remains_pending(tmp_path):
    q=tmp_path/"q"; q.mkdir()
    (q/"x.json").write_text(json.dumps({"task_key":"x","repository":"jweter/x"}))
    assert reconcile_laptop_queue(q,tmp_path/"r")[0]["status"]=="PENDING"

def test_claude_result_updates_measurement_and_scorecard(tmp_path):
    state=tmp_path/"c"
    rec=seed_external_dispatch(state,routine="adversarial_reviewer",repository="portfolio",task_key="review-1",
        session_id="cse_1",session_url="https://claude.ai/code/cse_1",started_at=time.time()-1)
    out=reconcile_claude_result(state,rec.dispatch_id,{"outcome_kind":"REPAIR_PR","repository":"jweter/x",
        "pr_url":"https://github.com/jweter/x/pull/1","verification":"CI_GREEN","product_advancement":True})
    assert out["status"]=="PASS"
    snap=reconciliation_snapshot(state,tmp_path/"q",tmp_path/"r")
    assert snap["claude"]["scorecard"]["adversarial_reviewer"]["advancement_rate"]==1.0
    assert snap["ready_for_next_work"] is True

def test_pending_execution_blocks_next_work_signal(tmp_path):
    state=tmp_path/"c"
    seed_external_dispatch(state,routine="deep_product",repository="portfolio",task_key="deep-1",
        session_id="cse_2",session_url="https://claude.ai/code/cse_2",started_at=time.time())
    snap=reconciliation_snapshot(state,tmp_path/"q",tmp_path/"r")
    assert snap["claude"]["pending"]==1
    assert snap["ready_for_next_work"] is False
