from pathlib import Path
from project_orchestrator.execution_runtime import LaptopActorRuntime

def test_unreal_discovery_prefers_explicit_config(monkeypatch,tmp_path):
    editor=tmp_path/"UnrealEditor.exe"; editor.write_text("")
    monkeypatch.setenv("UNREAL_EDITOR",str(editor))
    assert LaptopActorRuntime.unreal_editor_path()==editor
    assert "unreal" in LaptopActorRuntime().attest()

def test_unreal_discovery_finds_epic_install(monkeypatch,tmp_path):
    monkeypatch.delenv("UNREAL_EDITOR",raising=False)
    monkeypatch.setenv("ProgramFiles",str(tmp_path))
    editor=tmp_path/"Epic Games"/"UE_5.8"/"Engine"/"Binaries"/"Win64"/"UnrealEditor.exe"
    editor.parent.mkdir(parents=True); editor.write_text("")
    monkeypatch.setattr("shutil.which",lambda name:None)
    assert LaptopActorRuntime.unreal_editor_path()==editor

def test_unreal_discovery_does_not_attest_missing_binary(monkeypatch,tmp_path):
    monkeypatch.delenv("UNREAL_EDITOR",raising=False)
    monkeypatch.setenv("ProgramFiles",str(tmp_path))
    monkeypatch.setattr("shutil.which",lambda name:None)
    assert LaptopActorRuntime.unreal_editor_path() is None
    assert "unreal" not in LaptopActorRuntime().attest()
