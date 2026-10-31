import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "laptop_actor_runner.py"


def decoy_package(tmp_path):
    """Stand-in for Azathoth's unrelated editable project_orchestrator install."""
    root = tmp_path / "decoy"
    package = root / "project_orchestrator"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "raise RuntimeError('DECOY_PROJECT_ORCHESTRATOR_IMPORTED')\n", encoding="utf-8"
    )
    return root


def isolated_env(**extra):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(extra)
    return env


def test_runner_imports_its_own_checkout_ahead_of_a_foreign_install(tmp_path):
    decoy = decoy_package(tmp_path)
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--help"],
        cwd=tmp_path,
        env=isolated_env(PYTHONPATH=str(decoy)),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "DECOY_PROJECT_ORCHESTRATOR_IMPORTED" not in result.stderr


def test_runner_refuses_an_already_loaded_foreign_package(tmp_path):
    foreign = tmp_path / "foreign"
    (foreign / "project_orchestrator").mkdir(parents=True)
    (foreign / "project_orchestrator" / "__init__.py").write_text("", encoding="utf-8")
    code = (
        "import runpy, sys;"
        f"sys.path.insert(0, {str(foreign)!r});"
        "import project_orchestrator;"
        f"runpy.run_path({str(RUNNER)!r}, run_name='not_main')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=isolated_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode != 0
    assert "IMPORT_PROVENANCE_MISMATCH" in result.stderr
