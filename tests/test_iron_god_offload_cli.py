"""Two independent Python processes exchange a genuine hashed code patch.

No production credentials or owner controller are involved; the final import
gate uses isolated explicit fixture callbacks and never publishes the patch.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

from project_orchestrator import iron_god_offload as offload
from project_orchestrator import iron_god_offload_cli as cli


def _git(path: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(path), *args],
                          capture_output=True, check=True).stdout


def _world(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "src").mkdir()
    (repo / "src" / "unit.py").write_text("def answer():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=Canary", "-c", "user.email=test@example.invalid",
         "commit", "-q", "-m", "base")
    sha = _git(repo, "rev-parse", "HEAD").decode().strip()
    db = tmp_path / "mail" / "coordination.sqlite"
    db.parent.mkdir()
    offer = offload.offer_slice(
        db, repository="jweter/VoiceStudio", slice_id="VS-HANDOFF-1", base_sha=sha,
        allowed_paths=["src/unit.py"], authority_ref="issue:747",
        failure_ref="evidence:verified-previous-attempt",
    )
    (repo / "src" / "unit.py").write_text("def answer():\n    return 2\n", encoding="utf-8")
    patch = _git(repo, "diff", "HEAD")
    _git(repo, "restore", "--", "src/unit.py")
    return repo, db, offer, patch


def _call(args: list[str], env: dict[str, str], patch: bytes = b""):
    result = subprocess.run(
        [sys.executable, "-m", "project_orchestrator.iron_god_offload_cli", *args],
        input=patch, env=env, capture_output=True, check=False, timeout=20,
    )
    assert result.stderr == b"", result.stderr.decode(errors="replace")
    return result.returncode, json.loads(result.stdout)


def test_cli_missing_configuration_or_write_switch_is_nonmutating(tmp_path):
    out = io.StringIO()
    assert cli.run(["list"], env={}, out=out) == 2
    assert json.loads(out.getvalue())["status"] == "CONFIGURATION_REQUIRED"
    repo, db, offer, patch = _world(tmp_path)
    env = {cli.DB_ENV: str(db)}
    for op in ("claim", "seal"):
        out = io.StringIO()
        assert cli.run([op, "--id", offer["offer_id"]], env=env,
                       out=out, patch_input=patch) == 1
        assert json.loads(out.getvalue())["status"] == "READ_ONLY"
    assert offload.list_offload_proposals(db)["items"][0]["status"] == "PROPOSED"


def test_two_real_cli_processes_stage_exact_patch_for_independent_git_import(tmp_path):
    repo, db, offer, patch = _world(tmp_path)
    stage = tmp_path / "private-staging"
    env = {**os.environ, cli.DB_ENV: str(db), cli.STAGE_ENV: str(stage),
           cli.WRITE_ENV: "1"}
    read_only = {**env, cli.WRITE_ENV: "0"}
    rc, seen = _call(["show", "--id", offer["offer_id"]], read_only)
    assert rc == 0 and seen["result"]["base_sha"] == offer["base_sha"]
    rc, claim = _call(["claim", "--id", offer["offer_id"]], env)
    assert rc == 0 and claim["result"]["worker_lease_granted"] is False
    rc, sealed = _call(["seal", "--id", offer["offer_id"]], env, patch)
    assert rc == 0 and sealed["result"]["status"] == "STAGED_UNVERIFIED"
    rc, repeated = _call(["seal", "--id", offer["offer_id"]], env, patch)
    assert rc == 0 and repeated["result"]["patch_sha256"] == sealed["result"]["patch_sha256"]
    rc, visible = _call(["list"], read_only)
    assert rc == 0 and visible["result"]["items"][0]["status"] == "STAGED_UNVERIFIED"
    digest = sealed["result"]["patch_sha256"]
    candidate = {
        "offer_id": offer["offer_id"], "patch_sha256": digest,
        "manifest_sha256": sealed["result"]["manifest_sha256"],
        "manifest_path": str(stage / f"{offer['offer_id']}-{digest}.json"),
    }
    assert offload.verify_candidate(offload.read_offer(db, offer["offer_id"]), candidate) == patch
    receipt = offload.prepare_isolated_import(
        offer, candidate, db_path=db, stage_root=stage, clone=repo,
        destination=tmp_path / "isolated-import",
        authority_check=lambda: True, ownership_check=lambda: True,
    )
    assert receipt["status"] == "PREFLIGHT_UNVERIFIED"
    assert receipt["merge_authorized"] is False
    assert (tmp_path / "isolated-import" / "src" / "unit.py").read_text() == (
        "def answer():\n    return 2\n"
    )
    assert (repo / "src" / "unit.py").read_text() == "def answer():\n    return 1\n"
    check = subprocess.run(
        [sys.executable, "-m", "py_compile",
         str(tmp_path / "isolated-import" / "src" / "unit.py")],
        capture_output=True, check=False,
    )
    assert check.returncode == 0
    assert _git(repo, "status", "--porcelain") == b""
