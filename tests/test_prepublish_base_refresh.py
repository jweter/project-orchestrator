import subprocess
from types import SimpleNamespace

import scripts.autonomous_slice_builder as builder
import project_orchestrator.builder_promotion as promotion


def cp(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(["cmd"],returncode,stdout,stderr)


def test_refresh_base_before_publish_keeps_verified_head_when_base_unchanged(tmp_path,monkeypatch):
    calls=[]
    def fake_run(argv,cwd,**kwargs):
        calls.append(argv)
        if argv[:3]==["git","fetch","origin"]:
            return cp()
        if argv==["git","rev-parse","origin/main"]:
            return cp("base-a")
        if argv==["git","rev-parse","HEAD"]:
            return cp("candidate-a")
        raise AssertionError(argv)
    monkeypatch.setattr(builder,"run",fake_run)
    result=builder.refresh_base_before_publish(tmp_path,"main","base-a")
    assert result=={"status":"UNCHANGED","base_sha":"base-a","head_sha":"candidate-a"}
    assert not any(call[:2]==["git","rebase"] for call in calls)


def test_refresh_base_before_publish_rebases_to_latest_default_head(tmp_path,monkeypatch):
    heads=iter(["candidate-old","candidate-new"])
    calls=[]
    def fake_run(argv,cwd,**kwargs):
        calls.append(argv)
        if argv[:3]==["git","fetch","origin"]:
            return cp()
        if argv==["git","rev-parse","origin/main"]:
            return cp("base-new")
        if argv==["git","rev-parse","HEAD"]:
            return cp(next(heads))
        if argv==["git","rebase","base-new"]:
            return cp()
        raise AssertionError(argv)
    monkeypatch.setattr(builder,"run",fake_run)
    result=builder.refresh_base_before_publish(tmp_path,"main","base-old")
    assert result=={"status":"REBASED","base_sha":"base-new","head_sha":"candidate-new"}
    assert ["git","rebase","base-new"] in calls


def test_refresh_base_before_publish_aborts_conflicted_rebase(tmp_path,monkeypatch):
    calls=[]
    def fake_run(argv,cwd,**kwargs):
        calls.append(argv)
        if argv[:3]==["git","fetch","origin"]:
            return cp()
        if argv==["git","rev-parse","origin/main"]:
            return cp("base-new")
        if argv==["git","rev-parse","HEAD"]:
            return cp("candidate-old")
        if argv==["git","rebase","base-new"]:
            return cp(returncode=1,stderr="conflict")
        if argv==["git","rebase","--abort"]:
            return cp()
        raise AssertionError(argv)
    monkeypatch.setattr(builder,"run",fake_run)
    result=builder.refresh_base_before_publish(tmp_path,"main","base-old")
    assert result=={"status":"CONFLICT","base_sha":"base-new","head_sha":"candidate-old"}
    assert calls[-1]==["git","rebase","--abort"]


def test_refresh_base_before_publish_fails_closed_when_fetch_fails(tmp_path,monkeypatch):
    def fake_run(argv,cwd,**kwargs):
        if argv[:3]==["git","fetch","origin"]:
            return cp(returncode=1,stderr="offline")
        if argv==["git","rev-parse","HEAD"]:
            return cp("candidate-a")
        raise AssertionError(argv)
    monkeypatch.setattr(builder,"run",fake_run)
    result=builder.refresh_base_before_publish(tmp_path,"main","base-a")
    assert result=={"status":"FETCH_FAILED","base_sha":"base-a","head_sha":"candidate-a"}


def _promotion_kwargs(tmp_path, run_fn, stage_fn):
    grant=tmp_path/"grant.json"
    grant.write_text('{"grant_id":"g"}',encoding="utf-8")
    return {
        "ws":tmp_path,
        "state_path":tmp_path/"state.json",
        "repo":"o/r",
        "idx":0,
        "main_branch":"main",
        "branch":"actor/x",
        "base":"base",
        "head":"head",
        "paths":["src/x.py"],
        "slice_authority":{"capability_id":"C","slice_id":"S"},
        "execution_grant":grant,
        "run_exact_head_preflight_fn":lambda ws,head:(cp(),head,True),
        "reset_workspace_fn":lambda *a,**k:None,
        "retain_failure_lesson_fn":lambda *a,**k:{"fingerprint":"fp"},
        "bind_preflight_evidence_fn":lambda *a,**k:{"head_sha":"head","binding":"ok"},
        "review_candidate_fn":lambda *a,**k:SimpleNamespace(status="APPROVE",reason="ok"),
        "advance_grant_stage_fn":stage_fn,
        "refresh_base_before_publish_fn":lambda *a,**k:{"status":"UNCHANGED","base_sha":"base","head_sha":"head"},
        "changed_paths_fn":lambda *a,**k:["src/x.py"],
        "generated_change_failure_fn":lambda *a,**k:None,
        "run_fn":run_fn,
        "pr_number_from_url_fn":lambda value:9,
    }


def test_promotion_stops_before_push_when_candidate_verification_cannot_persist(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(promotion,"_record_verified_candidate",lambda *a,**k:False)
    result=promotion.promote_candidate(**_promotion_kwargs(
        tmp_path,
        lambda *a,**k:calls.append(a) or cp(),
        lambda *a,**k:True,
    ))

    assert result["status"]=="GRANT_VERIFICATION_EVIDENCE_REJECTED"
    assert calls==[]


def test_promotion_stops_before_pr_creation_when_pushed_stage_cannot_persist(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(promotion,"_record_verified_candidate",lambda *a,**k:True)
    def run_fn(argv,*a,**k):
        calls.append(argv)
        return cp()
    result=promotion.promote_candidate(**_promotion_kwargs(
        tmp_path,
        run_fn,
        lambda *a,**k:False,
    ))

    assert result["status"]=="GRANT_STAGE_TRANSITION_REJECTED"
    assert result["attempted_stage"]=="PUSHED"
    assert calls==[["git","push","-u","origin","actor/x"]]
    assert not any(call[:3]==["gh","pr","create"] for call in calls)


def test_promotion_preserves_pr_identity_when_pr_open_stage_persistence_fails(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(promotion,"_record_verified_candidate",lambda *a,**k:True)
    def run_fn(argv,*a,**k):
        calls.append(argv)
        if argv[:3]==["gh","pr","create"]:
            return cp("https://github.com/o/r/pull/9\n")
        return cp()
    def stage_fn(_grant,stage,**evidence):
        return stage=="PUSHED"

    result=promotion.promote_candidate(**_promotion_kwargs(
        tmp_path,
        run_fn,
        stage_fn,
    ))

    assert result["status"]=="GRANT_STAGE_TRANSITION_REJECTED"
    assert result["attempted_stage"]=="PR_OPEN"
    assert result["pr_number"]==9
    assert result["pr_url"]=="https://github.com/o/r/pull/9"
