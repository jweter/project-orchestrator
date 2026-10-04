from pathlib import Path
import scripts.autonomous_slice_builder as builder

class R:
    def __init__(self,returncode=0,stdout=""): self.returncode=returncode; self.stdout=stdout; self.stderr=""

def test_worker_workspace_uses_isolated_git_worktree(tmp_path,monkeypatch):
    canonical=tmp_path/"repo"; canonical.mkdir()
    calls=[]
    def fake_run(cmd,cwd,**kwargs):
        calls.append((cmd,Path(cwd)))
        if cmd[:3]==["git","worktree","add"]:
            Path(cmd[4]).mkdir(parents=True)
        if cmd[:3]==["git","remote","get-url"]:
            return R(stdout="https://github.com/jweter/repo.git")
        return R()
    monkeypatch.setattr(builder,"run",fake_run)
    monkeypatch.setattr(builder,"clean_generated_workspace_artifacts",lambda ws:None)
    ws=builder.ensure_workspace("jweter/repo",tmp_path,"worker-2")
    assert ws==tmp_path/".worker-worktrees"/"worker-2"/"repo"
    assert any(cmd[:3]==["git","worktree","add"] for cmd,_ in calls)
    assert ws!=canonical

def test_dirty_worker_workspace_preserves_and_reports_paths(tmp_path,monkeypatch):
    canonical=tmp_path/"repo"; canonical.mkdir()
    isolated=tmp_path/".worker-worktrees"/"worker-2"/"repo"; isolated.mkdir(parents=True)
    def fake_run(cmd,cwd,**kwargs):
        if cmd[:3]==["git","remote","get-url"]:
            return R(stdout="https://github.com/jweter/repo.git")
        if cmd[:3]==["git","status","--porcelain"]:
            return R(stdout=" M src/unique.py\n?? recovery.txt\n")
        return R()
    monkeypatch.setattr(builder,"run",fake_run)
    monkeypatch.setattr(builder,"clean_generated_workspace_artifacts",lambda ws:None)
    try:
        builder.ensure_workspace("jweter/repo",tmp_path,"worker-2")
    except RuntimeError as exc:
        assert str(exc)=="WORKSPACE_DIRTY_PRESERVED:M src/unique.py;?? recovery.txt"
    else:
        raise AssertionError("dirty worker workspace must fail closed")
