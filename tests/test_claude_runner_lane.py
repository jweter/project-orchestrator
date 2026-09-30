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


def test_claude_is_not_authorized_by_authentication_alone(monkeypatch):
    monkeypatch.delenv("AZATHOTH_ENABLE_CLAUDE",raising=False)
    monkeypatch.setattr(registry,"claude_code_healthy",lambda:True)
    manifests=registry.default_runner_registry()._manifests
    assert manifests["claude-code"].enabled is False


def test_claude_requires_explicit_authorization_and_health(monkeypatch):
    monkeypatch.setenv("AZATHOTH_ENABLE_CLAUDE","true")
    monkeypatch.setattr(registry,"claude_code_healthy",lambda:True)
    assert registry.default_runner_registry()._manifests["claude-code"].enabled is True
    monkeypatch.setattr(registry,"claude_code_healthy",lambda:False)
    assert registry.default_runner_registry()._manifests["claude-code"].enabled is False

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

def test_claude_capacity_classifier_recognizes_current_quota_messages():
    samples=[
        "You've hit your monthly spend limit — raise it at claude.ai/settings/usage",
        "You've hit your weekly limit — resets Oct 2, 2pm (America/Chicago)",
        "session limit resets later",
        "usage limit reached",
        "rate limit exceeded",
    ]
    assert all(builder.claude_capacity_limited(value) for value in samples)


def test_claude_capacity_classifier_does_not_hide_normal_failures():
    assert not builder.claude_capacity_limited("TypeError: expected mapping")
    assert not builder.claude_capacity_limited("canonical preflight failed")


def test_unknown_runner_fails_closed(tmp_path):
    import pytest
    with pytest.raises(RuntimeError,match="UNSUPPORTED_RUNNER"):
        builder.run_builder(tmp_path,"x","unknown")
