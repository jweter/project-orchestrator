import subprocess
import scripts.autonomous_slice_builder as builder

def cp(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(["cmd"],returncode,stdout,stderr)

def test_exact_head_preflight_runs_at_expected_head(tmp_path,monkeypatch):
    heads=iter([cp("candidate\n"),cp("candidate\n")])
    monkeypatch.setattr(builder,"run",lambda *a,**k:next(heads))
    called=[]
    monkeypatch.setattr(builder,"run_preflight",lambda ws:(called.append(ws) or cp()))
    result,verified,stable=builder.run_exact_head_preflight(tmp_path,"candidate")
    assert result.returncode==0
    assert verified=="candidate"
    assert stable is True
    assert called==[tmp_path]

def test_exact_head_preflight_rejects_wrong_head(tmp_path,monkeypatch):
    monkeypatch.setattr(builder,"run",lambda *a,**k:cp("base-head\n"))
    called=[]
    monkeypatch.setattr(builder,"run_preflight",lambda ws:(called.append(ws) or cp()))
    result,verified,stable=builder.run_exact_head_preflight(tmp_path,"candidate-head")
    assert result.returncode==1
    assert "PREFLIGHT_HEAD_MISMATCH" in result.stderr
    assert verified=="base-head"
    assert stable is False
    assert called==[]
