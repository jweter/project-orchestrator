import json
from project_orchestrator.execution_dispatch import execute_actor_selection

class Response:
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def read(self): return json.dumps({"claude_code_session_id":"cse_auto","claude_code_session_url":"https://claude.ai/code/cse_auto"}).encode()

def test_closed_decision_dispatches_claude_and_returns_session(tmp_path,monkeypatch):
    monkeypatch.setenv("CLAUDE_ROUTINE_DEEP_PRODUCT_TOKEN","secret")
    task={"kind":"DEEP_ARCHITECTURE","repository":"jweter/knowledge-engine-core","task_key":"issue-129","prompt":"solve"}
    out=execute_actor_selection(task,claude_state_dir=tmp_path/"claude",laptop_queue_dir=tmp_path/"queue",claude_opener=lambda *a,**k: Response())
    assert out["status"]=="DISPATCHED"
    assert out["session_id"]=="cse_auto"
    assert out["routine"]=="deep_product"

def test_local_selection_enters_durable_worker_queue(tmp_path):
    task={"kind":"DETERMINISTIC_FAILURE","local_capability_verified":True,"repository":"jweter/Project-Everward","task_key":"cmake-1","prompt":"repair"}
    out=execute_actor_selection(task,claude_state_dir=tmp_path/"claude",laptop_queue_dir=tmp_path/"queue")
    assert out["status"]=="QUEUED"
    saved=json.loads((tmp_path/"queue"/"cmake-1.json").read_text())
    assert saved["actor"]=="LAPTOP_ACTOR"

def test_local_queue_is_idempotent(tmp_path):
    task={"kind":"PRODUCT_SLICE","local_capability_verified":True,"repository":"jweter/x","task_key":"slice-1"}
    a=execute_actor_selection(task,claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q")
    b=execute_actor_selection(task,claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q")
    assert a["status"]=="QUEUED"
    assert b["status"]=="ALREADY_QUEUED"

def test_human_boundary_never_enqueues_or_dispatches(tmp_path):
    out=execute_actor_selection({"kind":"DEEP_ARCHITECTURE","requires_human":True},claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q")
    assert out["status"]=="HUMAN_BOUNDARY"
    assert not (tmp_path/"q").exists()
