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
