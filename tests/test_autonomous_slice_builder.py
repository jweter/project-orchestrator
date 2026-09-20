from pathlib import Path
from scripts import autonomous_slice_builder as b

def test_protected_governance_paths_blocked():
    assert b.protected_change(["DoWork.txt"])=="DoWork.txt"
    assert b.protected_change([".github/workflows/ci.yml"])==".github/workflows/ci.yml"
    assert b.protected_change(["docs/ARCHITECTURE.md"])=="docs/ARCHITECTURE.md"

def test_source_and_tests_are_allowed():
    assert b.protected_change(["src/app.py","tests/test_app.py"]) is None

def test_prompt_forbids_human_and_product_reality_boundaries():
    text=b.prompt_for("jweter/Project-Everward")
    assert "ONE small" in text
    assert "does not require a human decision" in text
    assert "Do not claim Product Reality" in text

def test_select_repo_rotates_and_skips_saturated_repos(monkeypatch,tmp_path:Path):
    monkeypatch.setattr(b,"lane_capacity",lambda repo: 0 if repo.endswith("project-orchestrator") else 1)
    chosen=b.select_repo(tmp_path/"state.json")
    assert chosen=="jweter/knowledge-engine-core"

def test_select_repo_returns_none_when_every_repo_saturated(monkeypatch,tmp_path:Path):
    monkeypatch.setattr(b,"lane_capacity",lambda repo: 0)
    assert b.select_repo(tmp_path/"state.json") is None

def test_one_open_pr_leaves_one_parallel_lane(monkeypatch):
    monkeypatch.setattr(b,"open_pr_count",lambda repo: 1)
    assert b.lane_capacity("jweter/knowledge-engine-core")==1

def test_two_open_prs_saturate_repo(monkeypatch):
    monkeypatch.setattr(b,"open_pr_count",lambda repo: 2)
    assert b.lane_capacity("jweter/knowledge-engine-core")==0

def test_prompt_excludes_paths_owned_by_open_pr(monkeypatch):
    from scripts.lane_leases import LaneLease
    monkeypatch.setattr(b,"open_pr_paths",lambda repo:[LaneLease(repo,"pr-1","x",("src/retrieval.py",),"2026-09-19T00:00:00+00:00")])
    text=b.prompt_for("jweter/knowledge-engine-core")
    assert "src/retrieval.py" in text
    assert "does not edit, rename, delete" in text


def test_run_aider_uses_argument_vector_and_local_ollama(monkeypatch,tmp_path):
    seen={}
    class R:
        returncode=0
    def fake_run(argv,cwd,**kwargs):
        seen["argv"]=argv; seen["cwd"]=cwd; seen["env"]=kwargs["env"]; return R()
    monkeypatch.setattr(b,"run",fake_run)
    b.run_aider(tmp_path,"one bounded slice")
    assert seen["argv"][0]=="aider"
    assert seen["argv"][seen["argv"].index("--message")+1]=="one bounded slice"
    assert seen["env"]["OLLAMA_API_BASE"]=="http://127.0.0.1:11434"


def test_installer_uses_current_user_startup_supervisor():
    installer=(Path(__file__).resolve().parents[1]/"scripts"/"install_continuous_flow.ps1").read_text(encoding="utf-8")
    assert '[Environment]::GetFolderPath("Startup")' in installer
    assert "ExponentialEngine-ContinuousFlow.cmd" in installer
    assert "continuous_flow_supervisor.py" in installer
    assert "Green PR merger: 60-second poll" in installer
    assert "Autonomous builder: 5-minute loop" in installer

def test_semantic_gate_rejects_placeholder_file(tmp_path):
    target=tmp_path/"path"/"to"/"filename.js"
    target.parent.mkdir(parents=True)
    target.write_text("// entire file content ...",encoding="utf-8")
    reason=b.semantic_quality_failure(tmp_path,["path/to/filename.js"])
    assert reason.startswith("generic_or_placeholder_path:")

def test_semantic_gate_rejects_orphan_source_change(tmp_path):
    (tmp_path/"feature.py").write_text("def useful(): return True\n",encoding="utf-8")
    assert b.semantic_quality_failure(tmp_path,["feature.py"])=="source_change_without_regression_test"

def test_semantic_gate_accepts_source_with_regression_test(tmp_path):
    (tmp_path/"src").mkdir(); (tmp_path/"tests").mkdir()
    (tmp_path/"src"/"feature.py").write_text("def useful(): return True\n",encoding="utf-8")
    (tmp_path/"tests"/"test_feature.py").write_text("def test_useful(): assert True\n",encoding="utf-8")
    assert b.semantic_quality_failure(tmp_path,["src/feature.py","tests/test_feature.py"]) is None

def test_diff_anomaly_blocks_large_destructive_change(monkeypatch,tmp_path):
    class R:
        returncode=0
        stdout="10\t100\tfile.py\n"
    monkeypatch.setattr(b,"run",lambda *args,**kwargs:R())
    assert b.diff_anomaly_failure(tmp_path,"a"*40)=="excessive_deletions:100"

def test_repair_prompt_contains_exact_rejection_reason():
    text=b.repair_prompt("jweter/project-orchestrator","AUTONOMOUS_POLICY_CONTRACT_WEAKENING_174:deleted_keys:x",["engineering/agent-action-guard.json"])
    assert "AUTONOMOUS_POLICY_CONTRACT_WEAKENING_174" in text
    assert "exactly ONE bounded repair attempt" in text
    assert "Do not weaken or replace configuration contracts" in text

def test_select_repo_respects_authorized_product(monkeypatch, tmp_path):
    import scripts.autonomous_slice_builder as builder
    monkeypatch.setattr(builder,"lane_capacity",lambda repo:1)
    assert builder.select_repo(tmp_path/"state.json",{"rocksmith"})=="jweter/rocksmith-cdlc-generator"

def test_select_repo_returns_none_when_no_product_authorized(monkeypatch, tmp_path):
    import scripts.autonomous_slice_builder as builder
    monkeypatch.setattr(builder,"lane_capacity",lambda repo:1)
    assert builder.select_repo(tmp_path/"state.json",set()) is None

def test_execution_grant_rejects_product_when_local_head_moved(tmp_path, monkeypatch):
    import json
    import scripts.autonomous_slice_builder as builder
    import hashlib
    ws=tmp_path/"rocksmith-cdlc-generator"; ws.mkdir()
    contract=tmp_path/"contract.json"; contract.write_text('{"products":{"rocksmith":["jweter/rocksmith-cdlc-generator"]}}',encoding="utf-8")
    digest=hashlib.sha256(contract.read_bytes()).hexdigest()
    activation=tmp_path/"hands-off-activation.json"; activation.write_text('{"schema_version":2}',encoding="utf-8")
    activation_digest=hashlib.sha256(activation.read_bytes()).hexdigest()
    grant=tmp_path/"grant.json"
    grant.write_text(json.dumps({"schema_version":4,"grant_id":"test-grant","contract_sha256":digest,"activation_sha256":activation_digest,"products":{"rocksmith":{"repositories":{
        "jweter/rocksmith-cdlc-generator":"certified"}}}}),encoding="utf-8")
    class Result:
        returncode=0; stdout="moved\n"
    monkeypatch.setattr(builder,"run",lambda *a,**k:Result())
    assert builder.granted_repositories(grant,tmp_path,contract)=={}

def test_execution_grant_accepts_exact_local_head(tmp_path, monkeypatch):
    import json
    import scripts.autonomous_slice_builder as builder
    import hashlib
    ws=tmp_path/"rocksmith-cdlc-generator"; ws.mkdir()
    contract=tmp_path/"contract.json"; contract.write_text('{"products":{"rocksmith":["jweter/rocksmith-cdlc-generator"]}}',encoding="utf-8")
    digest=hashlib.sha256(contract.read_bytes()).hexdigest()
    activation=tmp_path/"hands-off-activation.json"; activation.write_text('{"schema_version":2}',encoding="utf-8")
    activation_digest=hashlib.sha256(activation.read_bytes()).hexdigest()
    grant=tmp_path/"grant.json"
    grant.write_text(json.dumps({"schema_version":4,"grant_id":"test-grant","contract_sha256":digest,"activation_sha256":activation_digest,"products":{"rocksmith":{"repositories":{
        "jweter/rocksmith-cdlc-generator":"certified"}}}}),encoding="utf-8")
    class Result:
        returncode=0; stdout="certified\n"
    monkeypatch.setattr(builder,"run",lambda *a,**k:Result())
    assert builder.granted_repositories(grant,tmp_path,contract)=={"jweter/rocksmith-cdlc-generator":"certified"}

def test_grant_native_selection_needs_no_product_mapping(tmp_path, monkeypatch):
    import scripts.autonomous_slice_builder as builder
    monkeypatch.setattr(builder,"lane_capacity",lambda repo: 1)
    repo="jweter/future-product-repo"
    monkeypatch.setattr(builder,"PORTFOLIO_REPOS",(repo,))
    assert builder.select_repo(tmp_path/"state.json",eligible_repos={repo})==repo

def test_grant_repo_universe_is_not_limited_by_static_portfolio(tmp_path, monkeypatch):
    import scripts.autonomous_slice_builder as builder
    monkeypatch.setattr(builder,"lane_capacity",lambda repo: 1)
    monkeypatch.setattr(builder,"PORTFOLIO_REPOS",("jweter/old-static-repo",))
    granted={"jweter/new-contract-repo"}
    assert builder.select_repo(tmp_path/"state.json",eligible_repos=granted)=="jweter/new-contract-repo"

def test_empty_execution_grant_selects_nothing_even_with_static_portfolio(tmp_path, monkeypatch):
    import scripts.autonomous_slice_builder as builder
    monkeypatch.setattr(builder,"lane_capacity",lambda repo: 1)
    monkeypatch.setattr(builder,"PORTFOLIO_REPOS",("jweter/old-static-repo",))
    assert builder.select_repo(tmp_path/"state.json",eligible_repos=set()) is None

def test_execution_grant_rejects_contract_drift(tmp_path, monkeypatch):
    import hashlib, json
    import scripts.autonomous_slice_builder as builder
    ws=tmp_path/"rocksmith-cdlc-generator"; ws.mkdir()
    contract=tmp_path/"contract.json"; contract.write_text('{"products":{"rocksmith":["jweter/rocksmith-cdlc-generator"]}}',encoding="utf-8")
    digest=hashlib.sha256(contract.read_bytes()).hexdigest()
    activation=tmp_path/"hands-off-activation.json"; activation.write_text('{"schema_version":2}',encoding="utf-8")
    activation_digest=hashlib.sha256(activation.read_bytes()).hexdigest()
    grant=tmp_path/"grant.json"
    grant.write_text(json.dumps({"schema_version":4,"grant_id":"test-grant","contract_sha256":digest,"activation_sha256":activation_digest,"products":{"rocksmith":{"repositories":{
        "jweter/rocksmith-cdlc-generator":"certified"}}}}),encoding="utf-8")
    contract.write_text('{"products":{"rocksmith":["jweter/rocksmith-cdlc-generator","jweter/extra"]}}',encoding="utf-8")
    class Result:
        returncode=0; stdout="certified\n"
    monkeypatch.setattr(builder,"run",lambda *a,**k:Result())
    assert builder.granted_repositories(grant,tmp_path,contract)=={}

def test_execution_grant_rejects_undeclared_product_even_with_valid_contract_digest(tmp_path, monkeypatch):
    import hashlib, json
    import scripts.autonomous_slice_builder as builder
    ws=tmp_path/"ghost"; ws.mkdir()
    contract=tmp_path/"contract.json"
    contract.write_text('{"products":{"rocksmith":["jweter/rocksmith-cdlc-generator"]}}',encoding="utf-8")
    digest=hashlib.sha256(contract.read_bytes()).hexdigest()
    activation=tmp_path/"hands-off-activation.json"; activation.write_text('{"schema_version":2}',encoding="utf-8")
    activation_digest=hashlib.sha256(activation.read_bytes()).hexdigest()
    grant=tmp_path/"grant.json"
    grant.write_text(json.dumps({"schema_version":4,"grant_id":"test-grant","contract_sha256":digest,"activation_sha256":activation_digest,"products":{
        "ghost":{"repositories":{"jweter/ghost":"abc"}}}}),encoding="utf-8")
    class Result:
        returncode=0; stdout="abc\n"
    monkeypatch.setattr(builder,"run",lambda *a,**k:Result())
    assert builder.granted_repositories(grant,tmp_path,contract)=={}

def test_execution_grant_rejects_incomplete_declared_product_membership(tmp_path, monkeypatch):
    import hashlib, json
    import scripts.autonomous_slice_builder as builder
    (tmp_path/"knowledge-engine-core").mkdir()
    contract=tmp_path/"contract.json"
    contract.write_text('{"products":{"knowledge-engine":["jweter/knowledge-engine-core","jweter/knowledge-engine-web"]}}',encoding="utf-8")
    digest=hashlib.sha256(contract.read_bytes()).hexdigest()
    activation=tmp_path/"hands-off-activation.json"; activation.write_text('{"schema_version":2}',encoding="utf-8")
    activation_digest=hashlib.sha256(activation.read_bytes()).hexdigest()
    grant=tmp_path/"grant.json"
    grant.write_text(json.dumps({"schema_version":4,"grant_id":"test-grant","contract_sha256":digest,"activation_sha256":activation_digest,"products":{
        "knowledge-engine":{"repositories":{"jweter/knowledge-engine-core":"abc"}}}}),encoding="utf-8")
    class Result:
        returncode=0; stdout="abc\n"
    monkeypatch.setattr(builder,"run",lambda *a,**k:Result())
    assert builder.granted_repositories(grant,tmp_path,contract)=={}

def test_execution_grant_rejects_activation_revocation(tmp_path, monkeypatch):
    import hashlib, json
    import scripts.autonomous_slice_builder as builder
    ws=tmp_path/"rocksmith-cdlc-generator"; ws.mkdir()
    contract=tmp_path/"contract.json"
    contract.write_text('{"products":{"rocksmith":["jweter/rocksmith-cdlc-generator"]}}',encoding="utf-8")
    contract_digest=hashlib.sha256(contract.read_bytes()).hexdigest()
    activation=tmp_path/"hands-off-activation.json"
    activation.write_text('{"schema_version":2,"reason":"READINESS_PASS"}',encoding="utf-8")
    activation_digest=hashlib.sha256(activation.read_bytes()).hexdigest()
    grant=tmp_path/"grant.json"
    grant.write_text(json.dumps({"schema_version":4,"grant_id":"test-grant","contract_sha256":contract_digest,
        "activation_sha256":activation_digest,"products":{"rocksmith":{"repositories":{
        "jweter/rocksmith-cdlc-generator":"certified"}}}}),encoding="utf-8")
    activation.write_text('{"schema_version":2,"reason":"READINESS_NOT_PASS"}',encoding="utf-8")
    class Result:
        returncode=0; stdout="certified\n"
    monkeypatch.setattr(builder,"run",lambda *a,**k:Result())
    assert builder.granted_repositories(grant,tmp_path,contract,activation)=={}

def test_execution_grant_is_single_use(tmp_path, monkeypatch):
    import hashlib, json
    import scripts.autonomous_slice_builder as builder
    ws=tmp_path/"rocksmith-cdlc-generator"; ws.mkdir()
    contract=tmp_path/"contract.json"
    contract.write_text('{"products":{"rocksmith":["jweter/rocksmith-cdlc-generator"]}}',encoding="utf-8")
    activation=tmp_path/"hands-off-activation.json"
    activation.write_text('{"schema_version":2,"reason":"READINESS_PASS"}',encoding="utf-8")
    grant=tmp_path/"grant.json"
    grant.write_text(json.dumps({"schema_version":4,"grant_id":"cycle-1",
        "contract_sha256":hashlib.sha256(contract.read_bytes()).hexdigest(),
        "activation_sha256":hashlib.sha256(activation.read_bytes()).hexdigest(),
        "products":{"rocksmith":{"repositories":{"jweter/rocksmith-cdlc-generator":"certified"}}}}),encoding="utf-8")
    consumed=tmp_path/"lifecycle.json"
    class Result:
        returncode=0; stdout="certified\n"
    monkeypatch.setattr(builder,"run",lambda *a,**k:Result())
    assert builder.granted_repositories(grant,tmp_path,contract,activation,consumed)=={"jweter/rocksmith-cdlc-generator":"certified"}
    assert builder.granted_repositories(grant,tmp_path,contract,activation,consumed)=={"jweter/rocksmith-cdlc-generator":"certified"}
    assert builder.claim_execution_grant(grant,"jweter/rocksmith-cdlc-generator","certified",consumed)
    assert builder.granted_repositories(grant,tmp_path,contract,activation,consumed)=={}
    assert json.loads(consumed.read_text())["grants"]["cycle-1"]["state"]=="CLAIMED"

def test_finish_grant_lifecycle_records_terminal_outcome(tmp_path):
    import json
    import scripts.autonomous_slice_builder as builder
    grant=tmp_path/"hands-off-execution-grant.json"
    grant.write_text(json.dumps({"grant_id":"cycle-2"}),encoding="utf-8")
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{
        "cycle-2":{"state":"CLAIMED","claimed_at":"earlier"}}}),encoding="utf-8")
    builder.finish_grant_lifecycle(grant,{"status":"PR_OPEN","repository":"jweter/rock"})
    record=json.loads(lifecycle.read_text())["grants"]["cycle-2"]
    assert record["state"]=="COMPLETED"
    assert record["result_status"]=="PR_OPEN"
    assert record["repository"]=="jweter/rock"
    assert record["finished_at"]

def test_finish_grant_lifecycle_records_failure(tmp_path):
    import json
    import scripts.autonomous_slice_builder as builder
    grant=tmp_path/"hands-off-execution-grant.json"
    grant.write_text(json.dumps({"grant_id":"cycle-3"}),encoding="utf-8")
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{
        "cycle-3":{"state":"CLAIMED"}}}),encoding="utf-8")
    builder.finish_grant_lifecycle(grant,{"status":"PREFLIGHT_FAILED","repository":"jweter/rock"})
    record=json.loads(lifecycle.read_text())["grants"]["cycle-3"]
    assert record["state"]=="FAILED"
    assert record["result_status"]=="PREFLIGHT_FAILED"

def test_heartbeat_grant_renews_claim(tmp_path):
    import json, threading
    import scripts.autonomous_slice_builder as builder
    grant=tmp_path/"hands-off-execution-grant.json"
    grant.write_text(json.dumps({"grant_id":"g1"}),encoding="utf-8")
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{"g1":{
        "state":"CLAIMED","claimed_at":"2026-01-01T00:00:00+00:00",
        "last_heartbeat_at":"2026-01-01T00:00:00+00:00"}}}),encoding="utf-8")
    stop=threading.Event()
    thread=threading.Thread(target=builder.heartbeat_grant,args=(grant,stop,0.01))
    thread.start()
    import time; time.sleep(0.04); stop.set(); thread.join()
    record=json.loads(lifecycle.read_text())["grants"]["g1"]
    assert record["last_heartbeat_at"]!="2026-01-01T00:00:00+00:00"

def test_claim_execution_grant_records_exact_repository_sha(tmp_path):
    import json
    import scripts.autonomous_slice_builder as builder
    grant=tmp_path/"grant.json"; lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    grant.write_text(json.dumps({"grant_id":"g1"}),encoding="utf-8")
    assert builder.claim_execution_grant(grant,"jweter/rock","abc",lifecycle)
    record=json.loads(lifecycle.read_text())["grants"]["g1"]
    assert record["state"]=="CLAIMED"
    assert record["authorized_repositories"]==["jweter/rock"]
    assert record["expected_sha"]=="abc"
    assert not builder.claim_execution_grant(grant,"jweter/rock","abc",lifecycle)

def test_build_once_rejects_grant_when_pre_mutation_head_moved(tmp_path, monkeypatch):
    import scripts.autonomous_slice_builder as builder
    monkeypatch.setattr(builder,"windows_idle_seconds",lambda:None)
    monkeypatch.setattr(builder,"select_repo",lambda *a,**k:"jweter/rocksmith-cdlc-generator")
    ws=tmp_path/"rocksmith-cdlc-generator"; ws.mkdir()
    monkeypatch.setattr(builder,"ensure_workspace",lambda *a,**k:ws)
    class Result:
        returncode=0; stdout="moved\n"; stderr=""
    monkeypatch.setattr(builder,"run",lambda *a,**k:Result())
    result=builder.build_once(tmp_path,tmp_path/"state.json",eligible_repos={"jweter/rocksmith-cdlc-generator":"authorized"},
                              execution_grant=tmp_path/"grant.json")
    assert result["status"]=="EXECUTION_GRANT_HEAD_MISMATCH"
    assert result["expected_sha"]=="authorized"
    assert result["actual_sha"]=="moved"

def test_failure_lessons_compound_repeated_failure_into_promotion_candidate(tmp_path):
    import json
    import scripts.autonomous_slice_builder as builder
    state=tmp_path/"state.json"
    first=builder.retain_failure_lesson(state,"preflight","TypeError: expected mapping","PREFLIGHT_FAILED")
    second=builder.retain_failure_lesson(state,"preflight","  typeerror:   expected mapping  ","PREFLIGHT_FAILED")
    assert first["fingerprint"]==second["fingerprint"]
    assert second["occurrences"]==2
    assert second["promotion_candidate"] is True
    persisted=json.loads((tmp_path/"autonomous-failure-lessons.json").read_text())
    assert persisted["lessons"][second["fingerprint"]]["occurrences"]==2

def test_failure_fingerprint_separates_failure_stages():
    import scripts.autonomous_slice_builder as builder
    detail="same underlying text"
    assert builder.failure_fingerprint("preflight",detail)!=builder.failure_fingerprint("aider_execution",detail)

def test_problem_solving_guidance_is_injected_into_worker_prompt():
    import scripts.autonomous_slice_builder as builder
    prompt=builder.prompt_for("jweter/rocksmith-cdlc-generator")
    assert "Troubleshooting and engineering practice guidance:" in prompt
    assert "Preserve evidence first" in prompt
    assert "Form a falsifiable hypothesis" in prompt
    assert "Never promote a learned behavior" in prompt

def test_prior_failure_experience_prefers_recurring_recent_evidence(tmp_path):
    import json
    import scripts.autonomous_slice_builder as builder
    state=tmp_path/"state.json"
    lessons={"schema_version":1,"lessons":{
      "a":{"fingerprint":"a","stage":"preflight","occurrences":1,"last_seen":"2026-01-01","last_outcome":"FAILED","detail":"one"},
      "b":{"fingerprint":"b","stage":"preflight","occurrences":3,"last_seen":"2026-01-02","last_outcome":"FAILED","detail":"recurring"}
    }}
    (tmp_path/"autonomous-failure-lessons.json").write_text(json.dumps(lessons))
    experience=builder.prior_failure_experience(state,limit=1)
    assert "[b]" in experience and "occurrences=3" in experience
    assert "[a]" not in experience

def test_worker_prompt_receives_prior_failure_experience(tmp_path):
    import json
    import scripts.autonomous_slice_builder as builder
    state=tmp_path/"state.json"
    (tmp_path/"autonomous-failure-lessons.json").write_text(json.dumps({"schema_version":1,"lessons":{
      "fp":{"fingerprint":"fp","stage":"preflight","occurrences":2,"last_seen":"2026-01-02","last_outcome":"FAILED","detail":"known failure"}
    }}))
    prompt=builder.prompt_for("jweter/rocksmith-cdlc-generator",state)
    assert "Prior recurring failure experience" in prompt
    assert "[fp]" in prompt
    assert "evidence, not permission to bypass gates" in prompt

def test_verified_repair_attribution_requires_repeated_success_for_promotion(tmp_path):
    import json
    import scripts.autonomous_slice_builder as builder
    state=tmp_path/"state.json"
    lesson=builder.retain_failure_lesson(state,"preflight","TypeError: expected mapping","PREFLIGHT_FAILED")
    fp=lesson["fingerprint"]
    first=builder.record_verified_repair(state,fp,"update_contract_test","canonical preflight passed")
    second=builder.record_verified_repair(state,fp,"update_contract_test","canonical preflight passed")
    assert first["verified_successes"]==1 and first["promotion_candidate"] is False
    assert second["verified_successes"]==2 and second["promotion_candidate"] is True
    experience=builder.prior_failure_experience(state)
    assert "verified_repair=update_contract_test successes=2" in experience

def test_verified_repair_does_not_invent_unknown_failure(tmp_path):
    import scripts.autonomous_slice_builder as builder
    assert builder.record_verified_repair(tmp_path/"state.json","unknown","anything","passed")=={}

def test_promote_verified_repairs_materializes_only_repeated_verified_candidates(tmp_path):
    import json
    import scripts.autonomous_slice_builder as builder
    state=tmp_path/"state.json"
    lesson=builder.retain_failure_lesson(state,"preflight","known","PREFLIGHT_FAILED")
    fp=lesson["fingerprint"]
    builder.record_verified_repair(state,fp,"repair_once","passed")
    builder.record_verified_repair(state,fp,"repair_twice","passed")
    builder.record_verified_repair(state,fp,"repair_twice","passed")
    payload=builder.promote_verified_repairs(state)
    caps=list(payload["capabilities"].values())
    assert len(caps)==1
    assert caps[0]["strategy"]=="repair_twice"
    assert caps[0]["state"]=="CANDIDATE"
    assert caps[0]["execution_authorized"] is False

def test_repair_capability_identity_is_stable_and_idempotent(tmp_path):
    import scripts.autonomous_slice_builder as builder
    state=tmp_path/"state.json"
    fp=builder.retain_failure_lesson(state,"preflight","known","FAILED")["fingerprint"]
    builder.record_verified_repair(state,fp,"repair","passed")
    builder.record_verified_repair(state,fp,"repair","passed")
    first=builder.promote_verified_repairs(state)
    second=builder.promote_verified_repairs(state)
    assert first==second
    assert len(second["capabilities"])==1

def test_repository_identity_keeps_rotation_stable_when_grant_universe_changes(tmp_path, monkeypatch):
    import json
    import scripts.autonomous_slice_builder as builder
    state=tmp_path/"state.json"
    state.write_text(json.dumps({"last_repository":"jweter/knowledge-engine-web","last_repo_index":99}))
    monkeypatch.setattr(builder,"lane_capacity",lambda repo:1)
    eligible={
        "jweter/knowledge-engine-core":"sha1",
        "jweter/knowledge-engine-web":"sha2",
        "jweter/rocksmith-cdlc-generator":"sha3",
    }
    assert builder.select_repo(state,eligible_repos=eligible)=="jweter/rocksmith-cdlc-generator"

def test_missing_prior_repository_restarts_deterministically_not_by_stale_index(tmp_path, monkeypatch):
    import json
    import scripts.autonomous_slice_builder as builder
    state=tmp_path/"state.json"
    state.write_text(json.dumps({"last_repository":"jweter/knowledge-engine-ai","last_repo_index":2}))
    monkeypatch.setattr(builder,"lane_capacity",lambda repo:1)
    eligible={"jweter/rocksmith-cdlc-generator":"sha","jweter/Project-Everward":"sha"}
    assert builder.select_repo(state,eligible_repos=eligible)==sorted(eligible)[0]

def test_legacy_numeric_rotation_state_remains_readable(tmp_path, monkeypatch):
    import json
    import scripts.autonomous_slice_builder as builder
    state=tmp_path/"state.json"
    state.write_text(json.dumps({"last_repo_index":0}))
    monkeypatch.setattr(builder,"lane_capacity",lambda repo:1)
    eligible={"a/repo":"1","b/repo":"2"}
    assert builder.select_repo(state,eligible_repos=eligible)=="b/repo"

def test_claim_persists_concrete_worker_incarnation(tmp_path):
    import json
    import scripts.autonomous_slice_builder as builder
    grant=tmp_path/"grant.json"; lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    grant.write_text(json.dumps({"grant_id":"g"}))
    worker={"host":"h","pid":7,"process_started_ns":11,"lease_token":"token"}
    assert builder.claim_execution_grant(grant,"o/r","abc",worker,lifecycle)
    record=json.loads(lifecycle.read_text())["grants"]["g"]
    assert record["worker"]==worker
    assert record["state"]=="CLAIMED"

def test_stale_worker_cannot_heartbeat_new_incarnation(tmp_path, monkeypatch):
    import json, threading
    import scripts.autonomous_slice_builder as builder
    grant=tmp_path/"grant.json"; lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    grant.write_text(json.dumps({"grant_id":"g"}))
    current={"host":"h","pid":7,"process_started_ns":22,"lease_token":"new"}
    lifecycle.write_text(json.dumps({"grants":{"g":{"state":"CLAIMED","worker":current,"last_heartbeat_at":"before"}}}))
    stop=threading.Event()
    monkeypatch.setattr(stop,"wait",lambda interval: False)
    old={"host":"h","pid":7,"process_started_ns":11,"lease_token":"old"}
    builder.heartbeat_grant(grant,stop,old,interval=0)
    assert json.loads(lifecycle.read_text())["grants"]["g"]["last_heartbeat_at"]=="before"
