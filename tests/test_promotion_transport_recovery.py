from pathlib import Path
from types import SimpleNamespace
from project_orchestrator.builder_promotion import promote_candidate

def _call(tmp_path, *, push_rc=0, pr_rc=0):
    ws=tmp_path/"ws"; ws.mkdir()
    grant=tmp_path/"grant.json"; grant.write_text('{"grant_id":"g1"}')
    stages=[]
    def run(argv,_ws):
        if argv[:3]==["git","push","-u"]:
            return SimpleNamespace(returncode=push_rc,stdout="",stderr="blocked" if push_rc else "")
        return SimpleNamespace(returncode=pr_rc,stdout="https://github.com/jweter/x/pull/1" if not pr_rc else "",stderr="blocked" if pr_rc else "")
    result=promote_candidate(
        ws=ws,state_path=tmp_path/"state.json",repo="jweter/x",idx=0,main_branch="main",
        branch="actor/x",base="a"*40,head="b"*40,paths=["x.py"],slice_authority={},
        execution_grant=grant,
        run_exact_head_preflight_fn=lambda _w,h:(SimpleNamespace(returncode=0,stdout="",stderr=""),h,True),
        reset_workspace_fn=lambda *_:None,retain_failure_lesson_fn=lambda *_:{"fingerprint":"x"},
        bind_preflight_evidence_fn=lambda *_:{"head_sha":"b"*40},
        review_candidate_fn=lambda *_a,**_k:SimpleNamespace(status="APPROVE",reason="ok"),
        advance_grant_stage_fn=lambda _p,stage,**kw:stages.append((stage,kw)),
        refresh_base_before_publish_fn=lambda *_:{"status":"UNCHANGED","base_sha":"a"*40,"head_sha":"b"*40},
        changed_paths_fn=lambda *_:["x.py"],generated_change_failure_fn=lambda *_:None,
        run_fn=run,pr_number_from_url_fn=lambda _u:1)
    return result,stages

def test_push_failure_preserves_verified_candidate_for_transport_retry(tmp_path):
    result,stages=_call(tmp_path,push_rc=1)
    assert result["status"]=="PUSH_PENDING"
    assert result["head_sha"]=="b"*40
    assert stages[-1][0]=="PREFLIGHT_GREEN"
    assert stages[-1][1]["transport_state"]=="PUSH_PENDING"
    assert stages[-1][1]["transport_failure"]=="PUSH_FAILED"

def test_pr_creation_failure_preserves_pushed_exact_head_for_retry(tmp_path):
    result,stages=_call(tmp_path,pr_rc=1)
    assert result["status"]=="PR_OPEN_PENDING"
    assert stages[-1][0]=="PUSHED"
    assert stages[-1][1]["remote_head_sha"]=="b"*40
    assert stages[-1][1]["transport_state"]=="PR_OPEN_PENDING"

def test_supervisor_treats_transport_pending_as_live_promotion_work(tmp_path):
    import json
    from project_orchestrator.supervisor_runtime import builder_health
    state=tmp_path/"builder.json"
    for status in ("PUSH_PENDING","PR_OPEN_PENDING"):
        state.write_text(json.dumps({"status":status}),encoding="utf-8")
        assert builder_health(tmp_path,0)==(True,status)
