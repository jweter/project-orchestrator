"""Exercise the actual Windows launcher without starting the engine."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

PS = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(PS is None, reason="Windows PowerShell required")
LAUNCHER = Path(__file__).resolve().parents[1] / "scripts/launch_local_actor.ps1"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.mark.parametrize("fault", ["clean", "dirty", "wrong_head", "missing"])
def test_launcher_verifies_isolated_runtime_and_preserves_old_work(tmp_path, fault):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "Test")
    git(source, "config", "user.email", "test@example.invalid")
    (source / "scripts").mkdir()
    (source / "scripts/laptop_actor_bootstrap.py").write_text("raise RuntimeError('must not run')")
    git(source, "add", ".")
    git(source, "commit", "-qm", "release")
    sha = git(source, "rev-parse", "HEAD")
    runtime = tmp_path / "runtime"
    subprocess.run(["git", "clone", "-q", str(source), str(runtime)], check=True)
    old = tmp_path / "old-actor"
    old.mkdir()
    sentinel = old / "unique-work.txt"
    sentinel.write_text("preserve")
    if fault == "dirty":
        (runtime / "untracked.txt").write_text("unique")
    elif fault == "wrong_head":
        (runtime / "next.txt").write_text("next")
        git(runtime, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "add", ".")
        git(runtime, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "next")
    elif fault == "missing":
        runtime = tmp_path / "missing"
    config = tmp_path / "promotion.json"
    config.write_text(json.dumps(dict(enabled=True, accepted_repo=str(source),
                                     accepted_sha=sha, actor_repo=str(runtime))))
    result = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-File", str(LAUNCHER),
                             "-ConfigPath", str(config), "-ActorRoot", str(old), "-VerifyOnly"],
                            capture_output=True, text=True)
    assert (result.returncode == 0) is (fault == "clean"), result.stderr
    assert sentinel.read_text() == "preserve"
    if fault == "dirty":
        assert (runtime / "untracked.txt").read_text() == "unique"
