"""Security and integration tests for the fixed local Iron God read-only bridge."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator import iron_god_mcp_readonly_bridge as bridge
from project_orchestrator.iron_god_owner_control import OwnerDecision


@pytest.mark.parametrize("command", [
    "", "shell", "bash", "write_file", "take_the_wheel",
    "git status", "owner_status; Remove-Item C:\\", "../owner_status",
    "owner_status --root C:\\", "repository_head && echo hacked",
])
def test_unknown_command_never_reaches_shell_or_owner(tmp_path, monkeypatch, command):
    def forbidden(*args, **kwargs):
        raise AssertionError("unknown command may not access host")
    monkeypatch.setattr(bridge.subprocess, "run", forbidden)
    monkeypatch.setattr(bridge.owner_control, "evaluate", forbidden)
    result = bridge.run_readonly_command(
        command, state_root=tmp_path, repo_root=tmp_path,
        release_sha="a" * 40,
    )
    assert result == {
        "status": "REFUSED", "reason": "COMMAND_NOT_ALLOWLISTED",
        "can_execute": False,
    }


def test_fixed_capabilities_have_no_generic_shell_network_or_writes():
    result = bridge.readonly_capabilities()
    assert result["transport"] == "LOCAL_STDIO_ONLY"
    assert result["commands"] == ["engine_status", "owner_status", "repository_head"]
    assert not any(result[key] for key in ("generic_shell", "network_listener",
                                          "file_write", "can_execute"))
    assert result["advisory_only"] is True


def test_owner_oracle_read_only_and_redacts_identity(tmp_path, monkeypatch):
    calls = []

    def fake_evaluate(root, *, release_sha):
        calls.append((root, release_sha))
        return OwnerDecision(
            status="OWNER_CONTROL_ACTIVE", permitted=False,
            reasons=("OWNER_CONTROL_ACTIVE",), generation=12,
            command_id="never-expose-command-id", action="GIVE_ME_CONTROL",
            release_sha=release_sha, supervisor_instance_id="private-instance",
            revocation_acknowledged=True,
        )

    monkeypatch.setattr(bridge.owner_control, "evaluate", fake_evaluate)
    result = bridge.run_readonly_command(
        "owner_status", state_root=tmp_path, repo_root=tmp_path,
        release_sha="a" * 40,
    )
    assert calls == [(tmp_path / "IronGodControl", "a" * 40)]
    assert result["status"] == "OWNER_CONTROL_ACTIVE"
    assert result["data"]["revocation_acknowledged"] is True
    assert result["data"]["authenticated_owner_permission"] is False
    assert result["can_execute"] is False
    assert "command_id" not in result["data"]
    assert "supervisor_instance_id" not in result["data"]


def test_owner_oracle_requires_pinned_release_and_never_asks_controller(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no owner evaluation when pin is missing")
    monkeypatch.setattr(bridge.owner_control, "evaluate", forbidden)
    for sha in ("", "1234", "F" * 40, "a" * 40 + ";"):
        result = bridge.run_readonly_command(
            "owner_status", state_root=tmp_path, repo_root=tmp_path,
            release_sha=sha,
        )
        assert result["status"] == "UNVERIFIED"
        assert not result["data"]["authenticated_owner_permission"]


def test_unknown_owner_state_denies_without_creating_database(tmp_path):
    state_root = tmp_path / "flow-state"
    state_root.mkdir()
    result = bridge.run_readonly_command(
        "owner_status", state_root=state_root,
        repo_root=tmp_path, release_sha="a" * 40,
    )
    assert result["data"]["authenticated_owner_permission"] is False
    assert result["can_execute"] is False
    assert not (state_root / "IronGodControl").exists()


def test_engine_snapshot_is_reported_advisory_not_grant(tmp_path, monkeypatch):
    monkeypatch.setattr(
        bridge.coordination, "engine_snapshot",
        lambda _: {"sources": {"supervisor": {"status": "RUNNING_CURRENT",
                                              "evidence": "REPORTED_NOT_INDEPENDENTLY_VERIFIED"}},
                   "note": "advisory only"},
    )
    result = bridge.run_readonly_command(
        "engine_status", state_root=tmp_path, repo_root=tmp_path,
    )
    assert result["status"] == "OBSERVED"
    assert result["data"]["sources"]["supervisor"]["status"] == "RUNNING_CURRENT"
    assert result["source"] == "REPORTED_STATUS_NOT_OWNER_AUTHORITY"
    assert result["can_execute"] is False


def test_engine_snapshot_absent_sources_do_not_claim_health(tmp_path, monkeypatch):
    monkeypatch.setattr(
        bridge.coordination, "engine_snapshot",
        lambda _: {"sources": {"supervisor": {"status": "UNAVAILABLE"}},
                   "note": "reported state only"},
    )
    result = bridge.run_readonly_command(
        "engine_status", state_root=tmp_path, repo_root=tmp_path,
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["data"]["sources"]["supervisor"]["status"] == "UNAVAILABLE"
    assert result["can_execute"] is False


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_git_head_is_fixed_bounded_read_without_filter_execution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Readonly Bridge Test")
    _git(repo, "config", "user.email", "readonly@example.invalid")
    (repo / "sample.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "sample.txt")
    _git(repo, "commit", "-m", "fixture")
    expected = _git(repo, "rev-parse", "HEAD")
    clean = bridge.run_readonly_command(
        "repository_head", state_root=tmp_path, repo_root=repo,
    )
    assert clean["status"] == "OK"
    assert clean["data"]["head_sha"] == expected
    assert clean["data"]["tracked_worktree_dirty"] is None
    assert clean["data"]["tracked_change_detection"] == "NOT_EVALUATED_FILTER_EXECUTION_RISK"
    assert clean["can_execute"] is False
    (repo / "sample.txt").write_text("two\n", encoding="utf-8")
    dirty = bridge.run_readonly_command(
        "repository_head", state_root=tmp_path, repo_root=repo,
    )
    assert dirty["data"]["tracked_worktree_dirty"] is None
    # A malicious clean/process filter would execute on git diff/status. This
    # probe never invokes either operation, so even a modified tracked file
    # with a configured filter must not execute the injected command.
    (repo / ".gitattributes").write_text("sample.txt filter=probe\n", encoding="utf-8")
    _git(repo, "config", "filter.probe.clean", "echo EXECUTED > FILTER_EXECUTED")
    marker = repo / "FILTER_EXECUTED"
    assert not marker.exists()
    with_filter = bridge.run_readonly_command(
        "repository_head", state_root=tmp_path, repo_root=repo,
    )
    assert with_filter["status"] == "OK"
    assert with_filter["data"]["tracked_worktree_dirty"] is None
    assert not marker.exists()
    assert _git(repo, "rev-parse", "HEAD") == expected
    nested = repo / "nested"
    nested.mkdir()
    refused = bridge.run_readonly_command(
        "repository_head", state_root=tmp_path, repo_root=nested,
    )
    assert refused["status"] == "UNAVAILABLE"


def test_absolute_host_roots_are_mandatory(tmp_path):
    assert bridge.run_readonly_command(
        "repository_head", state_root=Path("relative"),
        repo_root=tmp_path,
    )["status"] == "REFUSED"
