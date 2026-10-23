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
    ledger=json.loads((tmp_path/"planner-decisions"/"rocksmith.json").read_text())
    assert ledger["candidate_count"]==3
    assert ledger["selected_count"]==2
    selected={row["slice_id"] for row in ledger["candidates"] if row["selected"]}
    rejected={row["slice_id"] for row in ledger["candidates"] if not row["selected"]}
    assert selected=={"A","B"}
    assert rejected=={"C"}

def test_issue_frontier_tasks_bind_authoritative_activation_identity(tmp_path):
    activation = {"products": {"rocksmith": {"repositories": {"jweter/repo": "abc"}}}}
    graphs = tmp_path / "capability-graphs"
    graphs.mkdir()
    (graphs / "jweter__repo.json").write_text(json.dumps(graph()))
    (tmp_path / "issue-work-frontier.json").write_text(json.dumps({
        "ranked_actionable": [
            {
                "product": "rocksmith",
                "repository": "jweter/repo",
                "work_id": "issue-159",
                "title": "Repair the verified frontier defect",
                "issue_number": 159,
                "url": "https://example.test/issue/159",
                "allowed_paths": ["src/repair/"],
                "exact_head": "untrusted-stale-sha",
            },
            {
                "product": "rocksmith",
                "repository": "jweter/not-registered",
                "work_id": "unregistered",
                "title": "Must not dispatch",
            },
        ]
    }))

    batch = supervisor.ready_task_batch(tmp_path, activation, "rocksmith", 4)
    issue = [(repo, head, task) for repo, head, task in batch
             if task["slice_id"] == "issue-159"]
    assert len(issue) == 1
    repo, head, task = issue[0]
    assert (repo, head) == ("jweter/repo", "abc")
    assert task["repository"] == repo
    assert task["exact_head"] == head
    assert task["source_kind"] == "ISSUE"
    assert all(task["slice_id"] != "unregistered" for _, _, task in batch)


def test_ready_task_batch_surfaces_missing_graph_as_direction_required(tmp_path):
    activation={"products":{"rocksmith":{"repositories":{"jweter/repo":"abc"}}}}
    batch=supervisor.ready_task_batch(tmp_path,activation,"rocksmith",4)
    assert batch==[]
    request=json.loads((tmp_path/"direction-requests"/"jweter__repo.json").read_text())
    assert request["reason"]=="CAPABILITY_GRAPH_REQUIRED"
    assert request["repository"]=="jweter/repo"
    assert request["exact_head"]=="abc"
    ledger=json.loads((tmp_path/"planner-decisions"/"rocksmith.json").read_text())
    assert ledger["candidate_count"]==0


def test_ready_task_batch_persists_non_executable_authority_reason(tmp_path):
    activation={"products":{"rocksmith":{"repositories":{"jweter/repo":"abc"}}}}
    d=tmp_path/"capability-graphs"; d.mkdir()
    payload=graph()
    payload["capabilities"][0]["slices"]=[]
    (d/"jweter__repo.json").write_text(json.dumps(payload))
    batch=supervisor.ready_task_batch(tmp_path,activation,"rocksmith",4)
    assert batch==[]
    request=json.loads((tmp_path/"direction-requests"/"jweter__repo.json").read_text())
    assert request["reason"]=="CAPABILITY_AUTHORITY_NOT_EXECUTABLE"
    assert request["issues"]==[{"code":"EXECUTION_SLICE_REQUIRED","capability":"C"}]


def test_ready_task_batch_ready_authority_clears_stale_direction_request(tmp_path):
    activation={"products":{"rocksmith":{"repositories":{"jweter/repo":"abc"}}}}
    d=tmp_path/"capability-graphs"; d.mkdir()
    (d/"jweter__repo.json").write_text(json.dumps(graph()))
    requests=tmp_path/"direction-requests"; requests.mkdir()
    (requests/"jweter__repo.json").write_text(json.dumps({"status":"DIRECTION_REQUIRED"}))
    batch=supervisor.ready_task_batch(tmp_path,activation,"rocksmith",4)
    assert batch
    assert not (requests/"jweter__repo.json").exists()


def test_dispatch_assigns_distinct_tasks_to_same_repo_workers(tmp_path,monkeypatch):
    activation={"products":{"rocksmith":{"repositories":{"jweter/repo":"abc"}}}}
    (tmp_path/"hands-off-activation.json").write_text(json.dumps(activation))
    d=tmp_path/"capability-graphs"; d.mkdir()
    (d/"jweter__repo.json").write_text(json.dumps(graph()))
    calls=[]
    monkeypatch.setattr(supervisor,"RUNTIME_LEASES",supervisor.RuntimeLeasePool(supervisor.DEFAULT_RUNTIME_CAPACITIES))
    def worker(*args,**kwargs):
        calls.append(args[-1])
        return (0,None)
    monkeypatch.setattr(supervisor,"run_builder_for_product",worker)
    pool=ParallelWorkerPool(max_workers_per_product=2,max_total_workers=2,executor_factory=ManualExecutor)
    from project_orchestrator.runner_registry import RunnerManifest,RunnerRegistry
    runners=RunnerRegistry([
        RunnerManifest("aider-ollama",frozenset({"code","local","windows","offline"}),1,30),
        RunnerManifest("claude-code",frozenset({"code","review","local","windows","offline"}),3,20),
    ])
    ids=supervisor.dispatch_parallel_builders(pool,tmp_path,tmp_path,tmp_path,["rocksmith"],runners)
    assert ids==["rocksmith-worker-1","rocksmith-worker-2"]
    submissions=pool._executor.submissions
    assert submissions[0][2][-1][2]["slice_id"]=="A"
    assert submissions[1][2][-1][2]["slice_id"]=="B"
    assert submissions[0][2][-1][2]["worker_id"]=="rocksmith-worker-1"
    assert submissions[1][2][-1][2]["worker_id"]=="rocksmith-worker-2"
    assert submissions[0][2][-1][2]["runner_id"]=="claude-code"
    assert submissions[1][2][-1][2]["runner_id"]=="aider-ollama"


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
    assert {r["planner_decision_id"] for r in lifecycle.values()}=={
        supervisor.planner_decision_id("rocksmith","jweter/repo","abc",a),
        supervisor.planner_decision_id("rocksmith","jweter/repo","abc",b),
    }

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
