import json
import scripts.continuous_flow_supervisor as supervisor
from project_orchestrator.parallel_worker_pool import ParallelWorkerPool
from tests.test_parallel_worker_pool import ManualExecutor

def graph():
    return {"capabilities":[{"id":"C","capability":"parallel","state":"PLANNED","depends_on":[],"completion_evidence":["tests"],
      "slices":[
       {"id":"A","state":"PLANNED","objective":"A","acceptance":["pass"],"allowed_paths":["src/a/"]},
       {"id":"B","state":"PLANNED","objective":"B","acceptance":["pass"],"allowed_paths":["src/b/"]},
       {"id":"C","state":"PLANNED","objective":"C","acceptance":["pass"],"allowed_paths":["src/a/nested/"]}]}],
       "roadmap":["C"]}

def test_ready_task_batch_allows_two_disjoint_tasks_same_repo(tmp_path):
    activation={"products":{"rocksmith":{"repositories":{"jweter/repo":"abc"}}}}
    d=tmp_path/"capability-graphs"; d.mkdir()
    (d/"jweter__repo.json").write_text(json.dumps(graph()))
    batch=supervisor.ready_task_batch(tmp_path,activation,"rocksmith",4)
    assert [x[2]["slice_id"] for x in batch]==["A","B"]

def test_dispatch_assigns_distinct_tasks_to_same_repo_workers(tmp_path,monkeypatch):
    activation={"products":{"rocksmith":{"repositories":{"jweter/repo":"abc"}}}}
    (tmp_path/"hands-off-activation.json").write_text(json.dumps(activation))
    d=tmp_path/"capability-graphs"; d.mkdir()
    (d/"jweter__repo.json").write_text(json.dumps(graph()))
    calls=[]
    def worker(*args,**kwargs):
        calls.append(args[-1])
        return (0,None)
    monkeypatch.setattr(supervisor,"run_builder_for_product",worker)
    pool=ParallelWorkerPool(max_workers_per_product=2,max_total_workers=2,executor_factory=ManualExecutor)
    ids=supervisor.dispatch_parallel_builders(pool,tmp_path,tmp_path,tmp_path,["rocksmith"])
    assert ids==["rocksmith-worker-1","rocksmith-worker-2"]
    submissions=pool._executor.submissions
    assert submissions[0][2][-1][2]["slice_id"]=="A"
    assert submissions[1][2][-1][2]["slice_id"]=="B"


def test_planned_grants_for_disjoint_same_repo_tasks_can_coexist(tmp_path,monkeypatch):
    repo_root=tmp_path/"control"; (repo_root/"engineering").mkdir(parents=True)
    (repo_root/"engineering"/"hands-off-evolution.json").write_text(json.dumps({"products":{"rocksmith":["jweter/repo"]}}))
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-activation.json").write_text(json.dumps({"products":{"rocksmith":{"repositories":{"jweter/repo":"abc"}}}}))
    lock={"path":state/"fake.lock","worker":{"host":"h","pid":1},"lock_id":"lock"}
    monkeypatch.setattr(supervisor,"acquire_grant_issue_lock",lambda *_:lock)
    monkeypatch.setattr(supervisor,"release_grant_issue_lock",lambda *_:None)
    a={"capability_id":"C","slice_id":"A","repository":"jweter/repo","exact_head":"abc",
       "objective":"A","acceptance":["pass"],"allowed_paths":["src/a/"]}
    b={"capability_id":"C","slice_id":"B","repository":"jweter/repo","exact_head":"abc",
       "objective":"B","acceptance":["pass"],"allowed_paths":["src/b/"]}
    ga=supervisor.write_execution_grant(repo_root,state,["rocksmith"],("jweter/repo","abc",a))
    gb=supervisor.write_execution_grant(repo_root,state,["rocksmith"],("jweter/repo","abc",b))
    assert ga!=gb
    lifecycle=json.loads((state/"hands-off-grant-lifecycle.json").read_text())["grants"]
    assert {r["slice_id"] for r in lifecycle.values()}=={"A","B"}

def test_planned_grant_rejects_stale_exact_head(tmp_path,monkeypatch):
    repo_root=tmp_path/"control"; (repo_root/"engineering").mkdir(parents=True)
    (repo_root/"engineering"/"hands-off-evolution.json").write_text(json.dumps({"products":{"rocksmith":["jweter/repo"]}}))
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-activation.json").write_text(json.dumps({"products":{"rocksmith":{"repositories":{"jweter/repo":"new"}}}}))
    lock={"path":state/"fake.lock","worker":{"host":"h","pid":1},"lock_id":"lock"}
    monkeypatch.setattr(supervisor,"acquire_grant_issue_lock",lambda *_:lock)
    monkeypatch.setattr(supervisor,"release_grant_issue_lock",lambda *_:None)
    task={"capability_id":"C","slice_id":"A","repository":"jweter/repo","exact_head":"old",
          "objective":"A","acceptance":["pass"],"allowed_paths":["src/a/"]}
    try:
        supervisor.write_execution_grant(repo_root,state,["rocksmith"],("jweter/repo","old",task))
    except RuntimeError as exc:
        assert str(exc)=="PLANNED_SLICE_HEAD_STALE"
    else:
        raise AssertionError("stale planned task must fail closed")

def test_grant_persists_runner_and_runtime_as_execution_authority(tmp_path,monkeypatch):
    repo_root=tmp_path/"control"; (repo_root/"engineering").mkdir(parents=True)
    (repo_root/"engineering"/"hands-off-evolution.json").write_text(json.dumps({"products":{"rocksmith":["jweter/repo"]}}))
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-activation.json").write_text(json.dumps({"products":{"rocksmith":{"repositories":{"jweter/repo":"abc"}}}}))
    lock={"path":state/"fake.lock","worker":{"host":"h","pid":1},"lock_id":"lock"}
    monkeypatch.setattr(supervisor,"acquire_grant_issue_lock",lambda *_:lock)
    monkeypatch.setattr(supervisor,"release_grant_issue_lock",lambda *_:None)
    task={"capability_id":"C","slice_id":"A","repository":"jweter/repo","exact_head":"abc",
          "objective":"A","acceptance":["pass"],"allowed_paths":["src/a/"],
          "runner_id":"aider-ollama","execution_runtime":"laptop-actor"}
    path=supervisor.write_execution_grant(repo_root,state,["rocksmith"],("jweter/repo","abc",task))
    grant=json.loads(path.read_text())
    assert grant["schema_version"]==5
    authority=grant["execution_slice"]
    assert authority["runner_id"]=="aider-ollama"
    assert authority["execution_runtime"]=="laptop-actor"
    lifecycle=json.loads((state/"hands-off-grant-lifecycle.json").read_text())["grants"][grant["grant_id"]]
    assert lifecycle["runner_id"]=="aider-ollama"
    assert lifecycle["execution_runtime"]=="laptop-actor"
