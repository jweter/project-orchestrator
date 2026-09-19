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
    assert builder.granted_repositories(grant,tmp_path,contract)==set()

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
    assert builder.granted_repositories(grant,tmp_path,contract)=={"jweter/rocksmith-cdlc-generator"}

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
    assert builder.granted_repositories(grant,tmp_path,contract)==set()

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
    assert builder.granted_repositories(grant,tmp_path,contract)==set()

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
    assert builder.granted_repositories(grant,tmp_path,contract)==set()

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
    assert builder.granted_repositories(grant,tmp_path,contract,activation)==set()

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
    assert builder.granted_repositories(grant,tmp_path,contract,activation,consumed)=={"jweter/rocksmith-cdlc-generator"}
    assert builder.granted_repositories(grant,tmp_path,contract,activation,consumed)==set()
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
