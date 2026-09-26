import subprocess
import scripts.autonomous_slice_builder as builder
import project_orchestrator.runner_registry as registry

def test_claude_health_uses_auth_status_without_model_generation(monkeypatch):
    monkeypatch.setattr(registry.shutil,"which",lambda name:r"C:\bin\claude.exe")
    seen={}
    def fake(argv,**kwargs):
        seen["argv"]=argv
        return subprocess.CompletedProcess(argv,0,'{"loggedIn":true}',"")
    monkeypatch.setattr(registry.subprocess,"run",fake)
    assert registry.claude_code_healthy()
    assert seen["argv"]==["claude","auth","status"]
    assert "-p" not in seen["argv"]

def test_claude_health_fails_closed_on_auth_error(monkeypatch):
    monkeypatch.setattr(registry.shutil,"which",lambda name:r"C:\bin\claude.exe")
    monkeypatch.setattr(registry.subprocess,"run",lambda *a,**k:subprocess.CompletedProcess(a[0],1,"","OAuth expired"))
    assert not registry.claude_code_healthy()

def test_claude_adapter_uses_noninteractive_edit_mode(monkeypatch,tmp_path):
    seen={}
    def fake(argv,cwd,**kwargs):
        seen["argv"]=argv; seen["cwd"]=cwd
        return subprocess.CompletedProcess(argv,0,"ok","")
    monkeypatch.setattr(builder,"run",fake)
    result=builder.run_builder(tmp_path,"implement slice","claude-code")
    assert result.returncode==0
    assert seen["argv"][:5]==["claude","-p","--output-format","text","--permission-mode"]
    assert seen["argv"][-1]=="implement slice"

def test_unknown_runner_fails_closed(tmp_path):
    import pytest
    with pytest.raises(RuntimeError,match="UNSUPPORTED_RUNNER"):
        builder.run_builder(tmp_path,"x","unknown")
