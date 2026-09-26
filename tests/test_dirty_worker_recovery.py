import subprocess
from pathlib import Path
import scripts.autonomous_slice_builder as builder

def git(cwd:Path,*args:str):
    return subprocess.run(["git",*args],cwd=cwd,text=True,capture_output=True,check=True)

def test_dirty_disposable_worker_recovery_preserves_residue_then_cleans(tmp_path):
    root=tmp_path/"workspaces"
    ws=root/".worker-worktrees"/"everward-worker-1"/"Project-Everward"
    ws.mkdir(parents=True)
    git(ws,"init")
    git(ws,"config","user.email","foundry@test.invalid")
    git(ws,"config","user.name","Foundry Test")
    (ws/"tracked.txt").write_text("base\n",encoding="utf-8")
    git(ws,"add","tracked.txt"); git(ws,"commit","-m","base")
    (ws/"tracked.txt").write_text("changed\n",encoding="utf-8")
    (ws/"new.txt").write_text("untracked\n",encoding="utf-8")

    recovery=builder.recover_dirty_worker_workspace(
        ws,root,"everward-worker-1","Project-Everward"
    )

    assert git(ws,"status","--porcelain").stdout==""
    patches=list(recovery.glob("*.patch"))
    assert len(patches)==1
    assert "changed" in patches[0].read_text(encoding="utf-8")
    saved=list(recovery.glob("*-untracked/new.txt"))
    assert len(saved)==1
    assert saved[0].read_text(encoding="utf-8")=="untracked\n"

def test_canonical_dirty_workspace_still_fails_closed(tmp_path,monkeypatch):
    root=tmp_path/"workspaces"; ws=root/"repo"; ws.mkdir(parents=True)
    responses=iter([
        subprocess.CompletedProcess([],0,"https://github.com/jweter/repo.git\n",""),
        subprocess.CompletedProcess([],0," M important.py\n",""),
    ])
    monkeypatch.setattr(builder,"run",lambda *a,**k:next(responses))
    import pytest
    with pytest.raises(RuntimeError,match="workspace dirty"):
        builder.ensure_workspace("jweter/repo",root)
