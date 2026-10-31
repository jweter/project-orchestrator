import hashlib
import json
from datetime import UTC, datetime

from project_orchestrator import recovery_manager
from project_orchestrator.recovery_manager import terminalize_abandoned_preclaim_quarantines
from project_orchestrator.grant_issuance import conflicting_nonterminal_grant
from tests.owner_authority import grant_owner_authority, revoke_owner_authority


def _write(path, grants):
    path.write_text(json.dumps({"schema_version": 1, "grants": grants}), encoding="utf-8")


def _conflicts(_left, _right):
    return True


def test_preclaim_abandoned_quarantine_becomes_non_owning(tmp_path):
    # Terminalization requires live, acknowledged owner authority.
    grant_owner_authority(tmp_path)
    lifecycle = tmp_path / "hands-off-grant-lifecycle.json"
    _write(lifecycle, {"g": {
        "state": "ABANDONED",
        "recovery_state": "QUARANTINED",
        "result_status": "BUILDER_EXITED_WITHOUT_CLAIM:1",
        "authorized_repositories": ["o/r"],
        "execution_slice": {"repository": "o/r", "slice_id": "s"},
    }})
    out = terminalize_abandoned_preclaim_quarantines(
        tmp_path, datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert out == {"g": "TERMINALIZED_PRECLAIM_NO_PRODUCT_WORK"}
    saved = json.loads(lifecycle.read_text())["grants"]["g"]
    assert saved["state"] == "ABANDONED"
    assert saved["recovery_state"] == "RECOVERED"
    assert saved["recovery_result"] == "TERMINAL_PRECLAIM_NO_PRODUCT_WORK"
    assert conflicting_nonterminal_grant(
        tmp_path, "o/r", {"repository": "o/r"}, _conflicts
    ) is None


def test_preserved_dirty_quarantine_stays_owning(tmp_path):
    # Terminalization requires live, acknowledged owner authority.
    grant_owner_authority(tmp_path)
    lifecycle = tmp_path / "hands-off-grant-lifecycle.json"
    _write(lifecycle, {"g": {
        "state": "ABANDONED",
        "recovery_state": "QUARANTINED",
        "result_status": "CLAIM_LEASE_EXPIRED_WORKER_DEAD",
        "repository": "o/r",
        "branch": "actor/recovery",
        "preserved_work_evidence": {"capture_status": "CAPTURED"},
        "authorized_repositories": ["o/r"],
        "execution_slice": {"repository": "o/r", "slice_id": "s"},
    }})
    assert terminalize_abandoned_preclaim_quarantines(tmp_path) == {}
    saved = json.loads(lifecycle.read_text())["grants"]["g"]
    assert saved["recovery_state"] == "QUARANTINED"
    assert conflicting_nonterminal_grant(
        tmp_path, "o/r", {"repository": "o/r"}, _conflicts
    )["grant_id"] == "g"


def test_ambiguous_preclaim_with_branch_is_not_released(tmp_path):
    # Terminalization requires live, acknowledged owner authority.
    grant_owner_authority(tmp_path)
    lifecycle = tmp_path / "hands-off-grant-lifecycle.json"
    _write(lifecycle, {"g": {
        "state": "ABANDONED",
        "recovery_state": "QUARANTINED",
        "result_status": "BUILDER_EXITED_WITHOUT_CLAIM:1",
        "branch": "actor/unexpected",
        "authorized_repositories": ["o/r"],
        "execution_slice": {"repository": "o/r", "slice_id": "s"},
    }})
    out = terminalize_abandoned_preclaim_quarantines(tmp_path)
    assert out == {"g": "PRESERVED_OR_AMBIGUOUS_QUARANTINE"}
    assert json.loads(lifecycle.read_text())["grants"]["g"]["recovery_state"] == "QUARANTINED"


def _preserved_record(diff_text: str):
    payload = diff_text.encode("utf-8")
    return {
        "state": "ABANDONED",
        "recovery_state": "QUARANTINED",
        "result_status": "CLAIM_LEASE_EXPIRED_WORKER_DEAD",
        "repository": "jweter/test-repo",
        "base_sha": "a" * 40,
        "preserved_work_revalidation": "PRESERVED_WORK_EXACT",
        "preserved_work_evidence": {
            "capture_status": "CAPTURED",
            "diff_sha256": hashlib.sha256(payload).hexdigest(),
            "diff_bytes": len(payload),
            "changed_paths": ["file.py"],
        },
        "authorized_repositories": ["jweter/test-repo"],
        "execution_slice": {"repository": "jweter/test-repo", "slice_id": "s"},
    }


def test_durable_remote_preservation_releases_old_ownership(tmp_path, monkeypatch):
    # Terminalization requires live, acknowledged owner authority.
    grant_owner_authority(tmp_path)
    lifecycle = tmp_path / "hands-off-grant-lifecycle.json"
    diff_text = "diff --git a/file.py b/file.py\n+preserved\n"
    _write(lifecycle, {"g": _preserved_record(diff_text)})
    repo = tmp_path / "repo"
    repo.mkdir()
    commit = "b" * 40

    def fake_git(_repo, *args):
        if args == ("remote", "get-url", "origin"):
            return "https://github.com/jweter/test-repo.git\n"
        if args == ("rev-parse", f"{commit}^{{commit}}"):
            return commit + "\n"
        if args == ("rev-parse", "origin/recovery/quarantine-test"):
            return commit + "\n"
        if args == ("diff", "--binary", "a" * 40, commit):
            return diff_text
        if args == ("diff", "--name-only", "a" * 40, commit):
            return "file.py\n"
        raise AssertionError(args)

    monkeypatch.setattr(recovery_manager, "_git_text", fake_git)
    proof = {
        "repository_path": str(repo),
        "commit_sha": commit,
        "remote_ref": "origin/recovery/quarantine-test",
    }
    out = terminalize_abandoned_preclaim_quarantines(
        tmp_path,
        datetime(2026, 1, 1, tzinfo=UTC),
        {"g": proof},
    )
    assert out == {"g": "TERMINALIZED_DURABLY_PRESERVED_WORK"}
    saved = json.loads(lifecycle.read_text())["grants"]["g"]
    assert saved["recovery_state"] == "RECOVERED"
    assert saved["preserved_recovery_commit"] == commit
    assert saved["preserved_recovery_ref"] == "origin/recovery/quarantine-test"
    assert conflicting_nonterminal_grant(
        tmp_path, "jweter/test-repo", {"repository": "jweter/test-repo"}, _conflicts
    ) is None


def test_bad_preservation_proof_is_atomic_and_keeps_quarantine(tmp_path, monkeypatch):
    # Terminalization requires live, acknowledged owner authority.
    grant_owner_authority(tmp_path)
    lifecycle = tmp_path / "hands-off-grant-lifecycle.json"
    diff_text = "diff --git a/file.py b/file.py\n+preserved\n"
    grants = {
        "g": _preserved_record(diff_text),
        "preclaim": {
            "state": "ABANDONED",
            "recovery_state": "QUARANTINED",
            "result_status": "BUILDER_EXITED_WITHOUT_CLAIM:1",
            "authorized_repositories": ["o/r"],
            "execution_slice": {"repository": "o/r", "slice_id": "s2"},
        },
    }
    _write(lifecycle, grants)
    repo = tmp_path / "repo"
    repo.mkdir()
    commit = "b" * 40

    def fake_git(_repo, *args):
        if args == ("remote", "get-url", "origin"):
            return "https://github.com/jweter/test-repo.git\n"
        if args[0:2] == ("rev-parse", f"{commit}^{{commit}}"):
            return commit + "\n"
        if args == ("rev-parse", "origin/recovery/quarantine-test"):
            return "c" * 40 + "\n"
        raise AssertionError(args)

    monkeypatch.setattr(recovery_manager, "_git_text", fake_git)
    proof = {
        "repository_path": str(repo),
        "commit_sha": commit,
        "remote_ref": "origin/recovery/quarantine-test",
    }
    out = terminalize_abandoned_preclaim_quarantines(tmp_path, preserved_proofs={"g": proof})
    assert out == {"g": "PRESERVATION_PROOF_REJECTED"}
    saved = json.loads(lifecycle.read_text())["grants"]
    assert saved["g"]["recovery_state"] == "QUARANTINED"
    assert saved["preclaim"]["recovery_state"] == "QUARANTINED"


def test_owner_control_active_suppresses_terminalization(tmp_path):
    lifecycle = tmp_path / "hands-off-grant-lifecycle.json"
    _write(lifecycle, {"g": {
        "state": "ABANDONED",
        "recovery_state": "QUARANTINED",
        "result_status": "BUILDER_EXITED_WITHOUT_CLAIM:1",
        "authorized_repositories": ["o/r"],
        "execution_slice": {"repository": "o/r", "slice_id": "s"},
    }})
    grant_owner_authority(tmp_path)
    revoke_owner_authority(tmp_path)  # owner pressed Give Me Control
    assert terminalize_abandoned_preclaim_quarantines(tmp_path) == {}
    assert json.loads(lifecycle.read_text())["grants"]["g"]["recovery_state"] == "QUARANTINED"


def test_absent_owner_controller_fails_closed(tmp_path):
    lifecycle = tmp_path / "hands-off-grant-lifecycle.json"
    _write(lifecycle, {"g": {
        "state": "ABANDONED",
        "recovery_state": "QUARANTINED",
        "result_status": "BUILDER_EXITED_WITHOUT_CLAIM:1",
        "authorized_repositories": ["o/r"],
        "execution_slice": {"repository": "o/r", "slice_id": "s"},
    }})
    # The retired marker grants nothing; with no controller state nothing is released.
    (tmp_path / "human-mode.json").write_text('{"enabled": false}\n', encoding="utf-8")
    assert terminalize_abandoned_preclaim_quarantines(tmp_path) == {}
    assert json.loads(lifecycle.read_text())["grants"]["g"]["recovery_state"] == "QUARANTINED"
