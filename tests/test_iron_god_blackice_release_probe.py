"""BLACKICE release ancestry probe: fail closed, no execution or install."""
from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from project_orchestrator import iron_god_blackice_release_probe as probe

R = probe.REPO
OLD = "a" * 40
MERGED = "b" * 40
NEW = "c" * 40


@pytest.fixture
def fixture():
    installer = b"fake unsigned installer data"
    digest = hashlib.sha256(installer).hexdigest()
    release = {
        "tag_name": "v2.40.73", "published_at": "2026-11-21T19:13:41Z",
        "assets": [{
            "name": "Blackice-2.40.73-Setup.exe", "digest": f"sha256:{digest}",
            "size": len(installer),
        }],
    }
    calls = {
        f"repos/{R}/releases/latest": release,
        f"repos/{R}/releases/tags/v2.40.73": release,
        f"repos/{R}/git/ref/tags/v2.40.73": {"object": {"type": "commit", "sha": OLD}},
        f"repos/{R}/pulls/24": {
            "number": 24, "merged_at": "2026-11-22T18:24:04Z",
            "merge_commit_sha": MERGED,
        },
        f"repos/{R}/compare/{MERGED}...{OLD}": {"status": "behind"},
        f"repos/{R}/compare/{MERGED}...{NEW}": {"status": "ahead"},
    }
    def api(route):
        return calls[route]
    return calls, api, installer


def test_old_release_predating_pr24_is_not_install_ready(fixture):
    _, api, _ = fixture
    out = probe.inspect_release(api=api)
    assert out["release_tag"] == "v2.40.73"
    assert out["source_gate"] == "RELEASE_PRECEDES_OR_DIVERGES_FROM_REQUIRED_PR"
    assert out["installer_integrity_gate"] == "NOT_CHECKED"
    assert out["ready_for_laptop_install"] is False
    assert out["ready_to_cancel_paid_remote"] is False
    assert out["unverified_independent_gates"]


def test_new_tag_with_verified_merge_still_does_not_claim_remote_canary(fixture):
    calls, api, _ = fixture
    calls[f"repos/{R}/git/ref/tags/v2.40.73"] = {
        "object": {"type": "commit", "sha": NEW},
    }
    out = probe.inspect_release(api=api, tag="v2.40.73")
    assert out["compare_status"] == "ahead"
    assert out["source_gate"] == "INCLUDES_REQUIRED_PR"
    assert out["ready_for_laptop_install"] is False
    assert out["ready_to_cancel_paid_remote"] is False
    assert "PLUS_HOSTED_CHAT_MCP_TOOL_INVOCATION" in out["unverified_independent_gates"]


def test_exact_local_installer_hash_is_not_a_signature_or_tool_proof(tmp_path, fixture):
    calls, api, data = fixture
    exe = tmp_path / "Blackice-2.40.73-Setup.exe"
    exe.write_bytes(data)
    out = probe.inspect_release(api=api, local_installer=exe)
    assert out["installer_bytes_checked"] is True
    assert out["installer_integrity_gate"] == "SHA256_MATCHES_GITHUB"
    assert out["ready_for_laptop_install"] is False
    calls[f"repos/{R}/releases/latest"]["assets"][0]["digest"] = "sha256:" + "0" * 64
    out2 = probe.inspect_release(api=api, local_installer=exe)
    assert out2["installer_integrity_gate"] == "SHA256_MISMATCH"
    assert out2["ready_for_laptop_install"] is False


def test_installer_size_mismatch_fails_closed(tmp_path, fixture):
    _, api, data = fixture
    exe = tmp_path / "Blackice-2.40.73-Setup.exe"
    exe.write_bytes(data[:-1])
    with pytest.raises(probe.ProbeError, match="SIZE_MISMATCH"):
        probe.inspect_release(api=api, local_installer=exe)


def test_symlink_is_refused(tmp_path, fixture):
    _, api, data = fixture
    original = tmp_path / "original.exe"
    original.write_bytes(data)
    link = tmp_path / "link.exe"
    try:
        link.symlink_to(original)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable on this Windows runner")
    with pytest.raises(probe.ProbeError, match="NOT_REGULAR_FILE"):
        probe.inspect_release(api=api, local_installer=link)


@pytest.mark.parametrize(("field", "value", "error"), [
    ("tag_name", "../bad", "INVALID_RELEASE_TAG"),
    ("tag_name", "main", "INVALID_RELEASE_TAG"),
    ("assets", [], "UNIQUE_WINDOWS_INSTALLER_REQUIRED"),
    ("assets", None, "RELEASE_ASSETS_UNAVAILABLE"),
])
def test_invalid_release_fails_closed(fixture, field, value, error):
    calls, api, _ = fixture
    calls[f"repos/{R}/releases/latest"][field] = value
    with pytest.raises(probe.ProbeError, match=error):
        probe.inspect_release(api=api)


@pytest.mark.parametrize("bad_tag", [
    "../main", "v2.40.73/../../", "v2.40", "v2.40.73;whoami",
])
def test_untrusted_tag_rejected_before_any_remote_query(fixture, bad_tag):
    _, api, _ = fixture
    with pytest.raises(probe.ProbeError, match="INVALID_RELEASE_TAG"):
        probe.inspect_release(api=api, tag=bad_tag)


def test_not_yet_merged_pr_refused(fixture):
    calls, api, _ = fixture
    calls[f"repos/{R}/pulls/24"]["merged_at"] = None
    with pytest.raises(probe.ProbeError, match="NOT_MERGED"):
        probe.inspect_release(api=api)


def test_untrusted_compare_or_tag_target_refused(fixture):
    calls, api, _ = fixture
    calls[f"repos/{R}/compare/{MERGED}...{OLD}"]["status"] = "mysterious"
    with pytest.raises(probe.ProbeError, match="LINEAGE_UNVERIFIED"):
        probe.inspect_release(api=api)
    calls[f"repos/{R}/git/ref/tags/v2.40.73"]["object"] = {
        "type": "blob", "sha": OLD,
    }
    with pytest.raises(probe.ProbeError, match="INVALID_RELEASE_TAG_TARGET"):
        probe.inspect_release(api=api)


def test_read_only_github_get_rejects_command_injection_without_launch(monkeypatch):
    def should_not_run(*args, **kwargs):
        raise AssertionError("No subprocess may launch")
    monkeypatch.setattr(subprocess, "run", should_not_run)
    for route in (
        f"repos/{R}/releases/latest;echo",
        f"repos/{R}/releases/latest?token=secret",
        "repos/attacker/OTHER/releases/latest",
        f"repos/{R}/../../admin",
    ):
        with pytest.raises(probe.ProbeError, match="UNSAFE_GITHUB_ENDPOINT"):
            probe.github_get(route)


def test_cli_reports_unverified_without_claiming_installed(capsys, monkeypatch):
    def unavailable(*args, **kwargs):
        raise probe.ProbeError("GITHUB_EVIDENCE_UNAVAILABLE")
    monkeypatch.setattr(probe, "inspect_release", unavailable)
    code = probe.main([])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert out["ready_to_cancel_paid_remote"] is False
    assert out["ready_for_laptop_install"] is False
    assert out["status"] == "UNVERIFIED"
