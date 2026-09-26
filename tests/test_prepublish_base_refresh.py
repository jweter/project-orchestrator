import subprocess
import scripts.autonomous_slice_builder as builder


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
