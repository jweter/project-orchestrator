"""Read-only source probe tests use disposable Git repositories only."""

import subprocess

from project_orchestrator.self_development_source_probe import (
    inspect_self_development_source,
)

ENGINE = "jweter/project-orchestrator"
CONTROLLER = {"mode": "candidate_mode", "candidate_execution_authorized": True}


def git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def prepared_repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "README.md").write_text("original\\n", encoding="utf-8")
    git(tmp_path, "add", "README.md")
    git(tmp_path, "commit", "-qm", "initial")
    return git(tmp_path, "rev-parse", "HEAD")


def activation(sha):
    return {"products": {"iron-god": {
        "enabled": True, "repositories": {ENGINE: sha}
    }}}


def test_exact_accepted_source_is_evidence_only(tmp_path):
    head = prepared_repo(tmp_path)
    git(tmp_path, "update-ref", "refs/iron-god/accepted", head)
    result = inspect_self_development_source(tmp_path, activation(head), CONTROLLER)
    assert result["status"] == "SOURCE_PROVENANCE_VERIFIED_ONLY"
    assert result["reasons"] == []
    assert result["dispatch_authorized"] is False


def test_missing_accepted_ref_blocks_without_creating_it(tmp_path):
    head = prepared_repo(tmp_path)
    result = inspect_self_development_source(tmp_path, activation(head), CONTROLLER)
    assert "PROTECTED_ACCEPTED_BASELINE_MISSING" in result["reasons"]
    assert "refs/iron-god/accepted" not in git(tmp_path, "show-ref")


def test_accepted_ref_must_point_to_commit_not_blob(tmp_path):
    head = prepared_repo(tmp_path)
    blob = git(tmp_path, "hash-object", "-w", "README.md")
    git(tmp_path, "update-ref", "refs/iron-god/accepted", blob)
    result = inspect_self_development_source(tmp_path, activation(head), CONTROLLER)
    assert result["status"] == "BLOCKED"
    assert result["accepted_sha"] is None
    assert "PROTECTED_ACCEPTED_BASELINE_NOT_COMMIT" in result["reasons"]
    assert result["dispatch_authorized"] is False
    assert git(tmp_path, "show-ref", "--verify", "--hash", "refs/iron-god/accepted") == blob


def test_stale_activation_is_not_accepted(tmp_path):
    original = prepared_repo(tmp_path)
    (tmp_path / "README.md").write_text("changed\\n", encoding="utf-8")
    git(tmp_path, "commit", "-qam", "second")
    git(tmp_path, "update-ref", "refs/iron-god/accepted", git(tmp_path, "rev-parse", "HEAD"))
    result = inspect_self_development_source(tmp_path, activation(original), CONTROLLER)
    assert "ACTIVATION_NOT_ACCEPTED_LOCAL_HEAD" in result["reasons"]


def test_shadow_controller_remains_blocked(tmp_path):
    head = prepared_repo(tmp_path)
    git(tmp_path, "update-ref", "refs/iron-god/accepted", head)
    result = inspect_self_development_source(
        tmp_path, activation(head),
        {"mode": "shadow_state_only", "candidate_execution_authorized": False},
    )
    assert "CANDIDATE_EXECUTION_NOT_AUTHORIZED" in result["reasons"]
    assert result["dispatch_authorized"] is False


def test_malformed_or_missing_activation_never_selects_default(tmp_path):
    prepared_repo(tmp_path)
    result = inspect_self_development_source(tmp_path, {}, CONTROLLER)
    assert result["selected_sha"] is None
    assert "EXACT_ACTIVATION_HEAD_MISSING" in result["reasons"]
