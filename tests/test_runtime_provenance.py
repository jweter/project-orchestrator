import hashlib
from project_orchestrator.execution_runtime import LaptopActorRuntime

def test_unreal_provenance_binds_resolved_binary(monkeypatch,tmp_path):
    editor=tmp_path/"UnrealEditor.exe"; editor.write_bytes(b"engine-binary")
    monkeypatch.setattr(LaptopActorRuntime,"unreal_editor_path",staticmethod(lambda:editor))
    monkeypatch.setattr("shutil.which",lambda name:None)
    p=LaptopActorRuntime().provenance()
    assert p["runtime_id"]=="laptop-actor"
    assert "unreal" in p["capabilities"]
    assert p["unreal_editor_path"]==str(editor.resolve())
    assert p["unreal_editor_sha256"]==hashlib.sha256(b"engine-binary").hexdigest()

def test_runtime_provenance_does_not_hash_unreal_for_non_unreal_task(monkeypatch,tmp_path):
    editor=tmp_path/"UnrealEditor.exe"; editor.write_bytes(b"engine-binary")
    monkeypatch.setattr(LaptopActorRuntime,"unreal_editor_path",staticmethod(lambda:editor))
    monkeypatch.setattr("shutil.which",lambda name:None)
    p=LaptopActorRuntime().provenance(["windows"])
    assert "unreal" in p["capabilities"]
    assert "unreal_editor_path" not in p
    assert "unreal_editor_sha256" not in p


def test_runtime_provenance_omits_unproven_optional_binary(monkeypatch):
    monkeypatch.setattr(LaptopActorRuntime,"unreal_editor_path",staticmethod(lambda:None))
    monkeypatch.setattr("shutil.which",lambda name:None)
    p=LaptopActorRuntime().provenance()
    assert p=={"runtime_id":"laptop-actor","capabilities":["local","offline","windows"]}
