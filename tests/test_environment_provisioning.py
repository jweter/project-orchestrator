from pathlib import Path

from project_orchestrator import environment_provisioning as env


def test_poetry_repo_gets_locked_install(monkeypatch, tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname='x'\n")
    monkeypatch.setattr(env.shutil, "which", lambda name: "C:/bin/" + name if name in {"python", "poetry"} else None)
    plan = env.provisioning_plan(tmp_path)
    assert plan["status"] == "READY"
    assert ["poetry", "install", "--no-interaction"] in plan["commands"]


def test_missing_required_manager_fails_closed(monkeypatch, tmp_path: Path):
    (tmp_path / "package-lock.json").write_text("{}")
    monkeypatch.setattr(env.shutil, "which", lambda name: None)
    plan = env.provisioning_plan(tmp_path)
    assert plan["status"] == "ENVIRONMENT_FAILURE"
    assert "package_lock_present_but_npm_unavailable" in plan["blockers"]


def test_no_manifest_never_invents_setup(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(env.shutil, "which", lambda name: None)
    plan = env.provisioning_plan(tmp_path)
    assert plan["commands"] == []
    assert plan["status"] == "READY"
