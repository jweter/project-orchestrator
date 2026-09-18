from pathlib import Path
from unittest.mock import patch, Mock

from project_orchestrator.environment_provisioning import provision_environment


def test_executes_deterministic_poetry_plan(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname='x'\nversion='0.1.0'\n")
    completed = Mock(returncode=0, stdout="ok", stderr="")
    with patch("project_orchestrator.environment_provisioning.shutil.which", return_value="tool"), patch("project_orchestrator.environment_provisioning.subprocess.run", return_value=completed) as run:
        result = provision_environment(tmp_path)
    assert result["status"] == "PROVISIONED"
    run.assert_called_once()
    assert run.call_args.args[0] == ["poetry", "install", "--no-interaction"]


def test_stops_on_provisioning_failure(tmp_path: Path):
    (tmp_path / "package-lock.json").write_text("{}")
    completed = Mock(returncode=1, stdout="", stderr="install failed")
    with patch("project_orchestrator.environment_provisioning.shutil.which", return_value="tool"), patch("project_orchestrator.environment_provisioning.subprocess.run", return_value=completed):
        result = provision_environment(tmp_path)
    assert result["status"] == "ENVIRONMENT_FAILURE"
    assert result["executed"][0]["returncode"] == 1


def test_missing_tool_fails_before_execution(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname='x'\nversion='0.1.0'\n")
    with patch("project_orchestrator.environment_provisioning.shutil.which", return_value=None), patch("project_orchestrator.environment_provisioning.subprocess.run") as run:
        result = provision_environment(tmp_path)
    assert result["status"] == "ENVIRONMENT_FAILURE"
    run.assert_not_called()
