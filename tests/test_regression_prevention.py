from project_orchestrator.regression_prevention import build_prevention_plan

def ev(repo,klass,ref,state="VERIFIED"):
    return {"repository":repo,"failure_class":klass,"source_ref":ref,"evidence_state":state}

def test_ready_after_three_verified_and_explicit_policy():
    repo="jweter/knowledge-engine-core"; events=[ev(repo,"FORMAT",str(i)) for i in range(3)]
    policies={repo:{"regression_prevention":{"approved_classes":["FORMAT"],"stage":"pre_commit","commands":{"FORMAT":["ruff","format","--check","."]}}}}
    assert build_prevention_plan(events,policies)["candidates"][0]["state"]=="READY"

def test_unverified_does_not_count():
    repo="jweter/x"; events=[ev(repo,"FORMAT","1"),ev(repo,"FORMAT","2","UNVERIFIED")]
    assert build_prevention_plan(events,{})["candidates"][0]["state"]=="INSUFFICIENT_EVIDENCE"

def test_behavioral_failure_never_candidate():
    assert build_prevention_plan([ev("jweter/x","TEST_FAILURE","x")],{})["candidates"]==[]

def test_threshold_without_repo_authority_blocks():
    repo="jweter/x"; events=[ev(repo,"IMPORT_SORT",str(i)) for i in range(3)]
    assert build_prevention_plan(events,{repo:{}})["candidates"][0]["state"]=="BLOCKED"
