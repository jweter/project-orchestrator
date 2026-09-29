from pathlib import Path
from types import SimpleNamespace

from project_orchestrator.builder_promotion import promote_candidate

def call(tmp_path, preflight_rc, learned):
    ws=tmp_path/"ws"; ws.mkdir()
    def preflight(_ws, head):
        return SimpleNamespace(returncode=preflight_rc,stdout="",stderr="bad" if preflight_rc else ""),head,True
    def run(argv,_ws):
        if argv[:3]==["git","push","-u"]:
            return SimpleNamespace(returncode=0,stdout="",stderr="")
        return SimpleNamespace(returncode=0,stdout="https://github.com/jweter/x/pull/1",stderr="")
    return promote_candidate(
        ws=ws,state_path=tmp_path/"state.json",repo="jweter/x",idx=0,
        main_branch="main",branch="b",base="base",head="head",paths=["x.py"],
        slice_authority=None,execution_grant=None,run_exact_head_preflight_fn=preflight,
        reset_workspace_fn=lambda *_:None,
        retain_failure_lesson_fn=lambda *_:{"fingerprint":"preflight-fp"},
        bind_preflight_evidence_fn=lambda *_:{},review_candidate_fn=lambda *_:None,
        advance_grant_stage_fn=lambda *_a,**_k:None,
        refresh_base_before_publish_fn=lambda *_:{"status":"UNCHANGED","base_sha":"base","head_sha":"head"},
        changed_paths_fn=lambda *_:["x.py"],generated_change_failure_fn=lambda *_:None,
        run_fn=run,pr_number_from_url_fn=lambda _url:1,
        verified_repair={"fingerprint":"repair-fp","strategy":"bounded"},
        record_verified_repair_fn=lambda *_a:learned.append("record"),
        promote_verified_repairs_fn=lambda *_a:learned.append("promote"),
    )

def test_failed_preflight_gets_no_learning_credit(tmp_path):
    learned=[]
    result=call(tmp_path,1,learned)
    assert result["status"]=="PREFLIGHT_FAILED"
    assert learned==[]

def test_verified_preflight_credits_learning_once(tmp_path):
    learned=[]
    result=call(tmp_path,0,learned)
    assert result["status"]=="PR_OPEN"
    assert learned==["record","promote"]
