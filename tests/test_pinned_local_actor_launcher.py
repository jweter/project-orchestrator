"""Exercise the real PowerShell local actor launcher without starting actors."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

LAUNCHER = (
    Path(__file__).resolve().parents[1] / "scripts" / "launch_local_actor.ps1"
)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True,
        text=True, check=True, timeout=20,
    ).stdout.strip()


@pytest.fixture
def release(tmp_path):
    source = tmp_path / "accepted"
    source.mkdir()
    _git(source, "init", "-q", "-b", "main")
    _git(source, "config", "user.name", "Test")
    _git(source, "config", "user.email", "test@example.invalid")
    scripts = source / "scripts"
    scripts.mkdir()
    (scripts / "laptop_actor_bootstrap.py").write_text(
        "# executable actor is never invoked in VerifyOnly tests\n",
        encoding="utf-8",
    )
    _git(source, "add", ".")
    _git(source, "commit", "-q", "-m", "accepted")
    sha = _git(source, "rev-parse", "HEAD")
    actor = tmp_path / "pinned-actor"
    subprocess.run(
        ["git", "clone", "-q", str(source), str(actor)],
        check=True, capture_output=True, timeout=20,
    )
    config = tmp_path / "pin.json"
    config.write_text(json.dumps({
        "enabled": True, "accepted_repo": str(source),
        "accepted_sha": sha, "actor_repo": str(actor),
    }), encoding="utf-8")
    return source, actor, sha, config


def _run(config, *options):
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(LAUNCHER),
         "-ConfigPath", str(config), "-VerifyOnly", *options],
        capture_output=True, text=True, check=False, timeout=30,
    )


def test_launcher_verify_only_uses_pinned_actor_not_legacy_default(release):
    source, actor, sha, config = release
    out = _run(config)
    assert out.returncode == 0, out.stdout + out.stderr
    assert f"LOCAL_ACTOR_PREFLIGHT_OK {sha}" in out.stdout
    assert str(actor) in out.stdout
    assert "actor-repo" not in out.stdout
    assert _git(actor, "rev-parse", "HEAD") == sha


def test_launcher_rejects_wrong_actorroot_override(release):
    _, actor, sha, config = release
    wrong = actor.parent / "old-stale-actor"
    out = _run(config, "-ActorRoot", str(wrong))
    assert out.returncode != 0
    assert "ActorRoot conflicts" in (out.stdout + out.stderr)


def test_launcher_rejects_untracked_code_in_accepted_source(release):
    source, actor, sha, config = release
    (source / "untracked-runtime.py").write_text("print('unsafe')\n", encoding="utf-8")
    out = _run(config)
    assert out.returncode != 0
    assert "Local accepted source is dirty" in (out.stdout + out.stderr)
    assert _git(actor, "rev-parse", "HEAD") == sha


def test_launcher_rejects_missing_pinned_actor_checkout(release):
    _, actor, sha, config = release
    record = json.loads(config.read_text(encoding="utf-8"))
    record.pop("actor_repo")
    config.write_text(json.dumps(record), encoding="utf-8")
    out = _run(config)
    assert out.returncode != 0
    assert "Pinned actor checkout missing" in (out.stdout + out.stderr)
    assert _git(actor, "rev-parse", "HEAD") == sha
