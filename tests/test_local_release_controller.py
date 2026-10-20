"""Real Git and fail-closed contract tests for locally staged actor releases."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from project_orchestrator import local_release_controller as release


def git(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd), *args], text=True,
                       capture_output=True, encoding="utf-8", check=True)
    return p.stdout.strip()


def source_repo(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "Local Release Test")
    git(source, "config", "user.email", "test@example.invalid")
    (source / "engineering").mkdir()
    (source / "engineering" / "preflight.py").write_text(
        "print('native preflight passed')\n", encoding="utf-8"
    )
    (source / "src.txt").write_text("tested software\n", encoding="utf-8")
    git(source, "add", ".")
    git(source, "commit", "-qm", "test baseline")
    return source, git(source, "rev-parse", "HEAD")


def mode_file(tmp_path, enabled=False):
    path = tmp_path / "human-mode.json"
    path.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
    return path


def staging(tmp_path, source, sha, marker):
    return release.stage_local_release(
        source, sha, tmp_path / "releases", tmp_path / "receipts", marker
    )


def test_verified_release_stages_exact_head_and_native_tests(tmp_path):
    source, sha = source_repo(tmp_path)
    marker = mode_file(tmp_path)
    result = staging(tmp_path, source, sha, marker)
    assert result["status"] == "VERIFIED_STAGED"
    assert result["accepted_sha"] == sha
    assert result["verification"]["exact_head"] == sha
    assert result["verification"]["exit_code"] == "0"
    assert result["verification"]["output_sha256"]
    assert result["live_supervisor_changed"] is False
    staged = Path(result["release_repo"])
    assert staged != source
    assert git(staged, "rev-parse", "HEAD") == sha
    assert release.validate_staged_release(result) is result
    assert json.loads((tmp_path / "receipts" / f"{sha}.json").read_text()) == result


def test_stage_is_idempotent_without_rerunning_preflight(monkeypatch, tmp_path):
    source, sha = source_repo(tmp_path)
    marker = mode_file(tmp_path)
    first = staging(tmp_path, source, sha, marker)
    monkeypatch.setattr(release, "_verify_native_preflight",
                        lambda *args: pytest.fail("duplicate stage cannot retest"))
    second = staging(tmp_path, source, sha, marker)
    assert second["status"] == "ALREADY_VERIFIED_STAGED"
    assert second["accepted_sha"] == first["accepted_sha"]


def test_rejects_stale_candidate_sha_before_test_or_staging(tmp_path):
    source, sha = source_repo(tmp_path)
    (source / "src.txt").write_text("next version\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "new version")
    with pytest.raises(release.ReleaseError, match="exact HEAD"):
        staging(tmp_path, source, sha, mode_file(tmp_path))
    assert not (tmp_path / "releases").exists()


@pytest.mark.parametrize("kind", ["tracked", "untracked"])
def test_rejects_dirty_candidate_without_reset(tmp_path, kind):
    source, sha = source_repo(tmp_path)
    if kind == "tracked":
        (source / "src.txt").write_text("uncommitted work\n")
    else:
        (source / "notes.txt").write_text("important untracked work\n")
    with pytest.raises(release.ReleaseError, match="uncommitted or untracked"):
        staging(tmp_path, source, sha, mode_file(tmp_path))
    assert (source / ("src.txt" if kind == "tracked" else "notes.txt")).exists()


@pytest.mark.parametrize("mode", ["missing", "malformed", "active", "null"])
def test_owner_control_fails_closed(tmp_path, mode):
    source, sha = source_repo(tmp_path)
    marker = tmp_path / "human-mode.json"
    if mode == "malformed":
        marker.write_text("broken", encoding="utf-8")
    elif mode == "active":
        marker.write_text('{"enabled": true}', encoding="utf-8")
    elif mode == "null":
        marker.write_text('{"enabled": null}', encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="owner control"):
        staging(tmp_path, source, sha, marker)
    assert not (tmp_path / "releases").exists()


def test_native_preflight_must_pass(tmp_path):
    source, sha = source_repo(tmp_path)
    (source / "engineering" / "preflight.py").write_text(
        "raise SystemExit(4)\n", encoding="utf-8"
    )
    git(source, "add", ".")
    git(source, "commit", "-qm", "failing preflight")
    sha = git(source, "rev-parse", "HEAD")
    with pytest.raises(release.ReleaseError, match="preflight FAILED"):
        staging(tmp_path, source, sha, mode_file(tmp_path))
    assert not (tmp_path / "releases").exists()


def test_partial_release_is_not_overwritten(tmp_path):
    source, sha = source_repo(tmp_path)
    marker = mode_file(tmp_path)
    releases = tmp_path / "releases"
    (releases / sha).mkdir(parents=True)
    sentinel = releases / sha / "owner-work.txt"
    sentinel.write_text("keep")
    with pytest.raises(release.ReleaseError, match="partial release"):
        staging(tmp_path, source, sha, marker)
    assert sentinel.read_text() == "keep"


def test_missing_or_tampered_release_is_not_activated(tmp_path):
    source, sha = source_repo(tmp_path)
    receipt = staging(tmp_path, source, sha, mode_file(tmp_path))
    (Path(receipt["release_repo"]) / "src.txt").write_text("tampered")
    with pytest.raises(release.ReleaseError, match="uncommitted"):
        release.readiness(receipt, current_sha="b" * 40,
                          active_workers=0, owner_marker=mode_file(tmp_path))


def test_readiness_refuses_active_workers_and_unknown_state(tmp_path):
    source, sha = source_repo(tmp_path)
    marker = mode_file(tmp_path)
    receipt = staging(tmp_path, source, sha, marker)
    assert release.readiness(receipt, current_sha="b"*40, active_workers=2,
                             owner_marker=marker)["status"] == "WAIT_FOR_WORKER_DRAIN"
    assert release.readiness(receipt, current_sha="b"*40, active_workers=None,
                             owner_marker=marker)["status"] == "BLOCKED_WORKER_STATE_UNKNOWN"
    assert release.readiness(receipt, current_sha=sha, active_workers=0,
                             owner_marker=marker)["status"] == "REVISION_ALREADY_LOADED"
    assert release.readiness(receipt, current_sha="b"*40, active_workers=0,
                             owner_marker=marker)["status"] == "ELIGIBLE_FOR_CONTROLLED_CUTOVER"


def test_readiness_does_not_grant_actual_cutover_authority(tmp_path):
    source, sha = source_repo(tmp_path)
    marker = mode_file(tmp_path)
    receipt = staging(tmp_path, source, sha, marker)
    assert receipt["authority"] == "STAGING_ONLY_NO_RUNTIME_DEPLOYMENT"
    assert not (tmp_path / "actor-repo").exists()
    assert not (tmp_path / "supervisor-process-instance.json").exists()
    assert not (tmp_path / "actor-local-promotion.json").exists()


def test_observe_supervisor_requires_fresh_bounded_identity(tmp_path):
    from datetime import datetime, timezone
    state = tmp_path / "state"
    state.mkdir()
    now = datetime.now(timezone.utc).isoformat()
    (state / "supervisor-generation-heartbeat.json").write_text(json.dumps({
        "actor_revision": "b" * 40, "heartbeat_at_utc": now,
    }))
    (state / "parallel-worker-pool.json").write_text(json.dumps({
        "recorded_at_utc": now, "pool": {"running_total": 2},
    }))
    assert release.observe_supervisor(state)["active_workers"] == 2
    (state / "parallel-worker-pool.json").write_text(json.dumps({
        "recorded_at_utc": "2026-01-01T00:00:00+00:00", "pool": {"running_total": 0},
    }))
    assert release.observe_supervisor(state)["state"] == "INSUFFICIENT_LIVE_EVIDENCE"


def test_observe_supervisor_rejects_missing_and_invalid_workers(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    assert release.observe_supervisor(state)["active_workers"] is None
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    (state / "supervisor-generation-heartbeat.json").write_text(json.dumps({
        "actor_revision": "b" * 40, "heartbeat_at_utc": now,
    }))
    (state / "parallel-worker-pool.json").write_text(json.dumps({
        "recorded_at_utc": now, "pool": {"running_total": True},
    }))
    assert release.observe_supervisor(state)["active_workers"] is None


def test_inspect_cli_does_not_mutate_actor(tmp_path, capsys):
    source, sha = source_repo(tmp_path)
    marker = mode_file(tmp_path)
    receipt = staging(tmp_path, source, sha, marker)
    path = tmp_path / "receipts" / (sha + ".json")
    state = tmp_path / "state"
    state.mkdir()
    assert release.main(["inspect", "--receipt", str(path),
                         "--state-root", str(state),
                         "--owner-marker", str(marker)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BLOCKED_WORKER_STATE_UNKNOWN"
    assert not (tmp_path / "actor-repo").exists()
    assert receipt["accepted_sha"] == sha


def test_invalid_receipt_fails_closed(tmp_path):
    source, sha = source_repo(tmp_path)
    marker = mode_file(tmp_path)
    receipt = staging(tmp_path, source, sha, marker)
    receipt["verification"]["exact_head"] = "b"*40
    with pytest.raises(release.ReleaseError, match="does not match"):
        release.validate_staged_release(receipt)
