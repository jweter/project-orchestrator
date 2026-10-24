"""Read-only activation audit regression tests use disposable Git repositories."""

import subprocess

from project_orchestrator.local_activation_audit import (
    audit_local_activation,
    verify_activation_for_dispatch,
)

REPO = "jweter/project-orchestrator"


def git(path, *args):
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def prepared(tmp_path):
    root = tmp_path / "project-orchestrator"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "README.md").write_text("initial\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-qm", "initial")
    git(root, "remote", "add", "origin", "https://github.com/jweter/project-orchestrator.git")
    return root, git(root, "rev-parse", "HEAD")


def activation(sha):
    return {
        "heads": {REPO: sha},
        "products": {"iron-god": {
            "enabled": True, "repositories": {REPO: sha},
        }},
    }


def audit(tmp_path, sha):
    return audit_local_activation([REPO], activation(sha), tmp_path)["repositories"][REPO]


def test_exact_local_accepted_head_is_diagnostic_only(tmp_path):
    root, head = prepared(tmp_path)
    git(root, "update-ref", "refs/iron-god/accepted", head)
    result = audit(tmp_path, head)
    assert result["status"] == "LOCAL_IDENTITY_VERIFIED_ONLY"
    assert result["reasons"] == []
    assert result["dispatch_authorized"] is False


def test_unavailable_activation_never_falls_back_to_checkout_head(tmp_path):
    root, head = prepared(tmp_path)
    git(root, "update-ref", "refs/iron-god/accepted", head)
    unavailable = "a" * 40
    result = audit(tmp_path, unavailable)
    assert "ACTIVATION_COMMIT_NOT_LOCAL" in result["reasons"]
    assert "ACTIVATION_NOT_ACCEPTED_LOCAL_HEAD" in result["reasons"]
    assert result["selected_sha"] == unavailable
    assert result["local_head"] == head


def test_missing_accepted_ref_never_created(tmp_path):
    root, head = prepared(tmp_path)
    result = audit(tmp_path, head)
    assert "ACCEPTED_REF_MISSING" in result["reasons"]
    assert result["accepted_sha"] is None
    assert subprocess.run(
        ["git", "-C", str(root), "show-ref", "--verify", "refs/iron-god/accepted"],
        capture_output=True, check=False,
    ).returncode != 0


def test_nested_path_cannot_impersonate_repository_root(tmp_path):
    root, head = prepared(tmp_path)
    git(root, "update-ref", "refs/iron-god/accepted", head)
    nested = root / "nested"
    nested.mkdir()
    result = audit_local_activation(
        ["jweter/nested"], {
            "heads": {"jweter/nested": head},
            "products": {"iron-god": {
                "enabled": True, "repositories": {"jweter/nested": head},
            }},
        }, root,
    )["repositories"]["jweter/nested"]
    assert result["status"] == "BLOCKED"
    assert "LOCAL_REPOSITORY_ROOT_MISMATCH" in result["reasons"]


def test_dirty_checkout_does_not_replace_accepted_head(tmp_path):
    root, head = prepared(tmp_path)
    git(root, "update-ref", "refs/iron-god/accepted", head)
    (root / "README.md").write_text("dirty\n", encoding="utf-8")
    result = audit(tmp_path, head)
    assert result["tracked_dirty"] is True
    assert result["accepted_sha"] == head
    assert result["dispatch_authorized"] is False


def test_top_level_head_disagreement_fails_closed(tmp_path):
    root, head = prepared(tmp_path)
    git(root, "update-ref", "refs/iron-god/accepted", head)
    payload = activation(head)
    payload["heads"][REPO] = "b" * 40
    result = audit_local_activation([REPO], payload, tmp_path)["repositories"][REPO]
    assert "ACTIVATION_INTERNAL_HEAD_MISMATCH" in result["reasons"]


def test_invalid_repository_id_never_traverses_workspace(tmp_path):
    result = audit_local_activation(
        ["jweter/.."], activation("a" * 40), tmp_path
    )["repositories"]["jweter/.."]
    assert result["reasons"] == ["INVALID_REPOSITORY_ID"]



def test_wrong_origin_blocks_local_identity_even_with_matching_sha(tmp_path):
    root, head = prepared(tmp_path)
    git(root, "update-ref", "refs/iron-god/accepted", head)
    git(root, "remote", "set-url", "origin", "https://github.com/other/project-orchestrator.git")
    result = audit(tmp_path, head)
    assert "LOCAL_REMOTE_REPOSITORY_MISMATCH" in result["reasons"]
    assert result["status"] == "BLOCKED"
    assert result["dispatch_authorized"] is False


def test_missing_origin_cannot_establish_repository_identity(tmp_path):
    root, head = prepared(tmp_path)
    git(root, "update-ref", "refs/iron-god/accepted", head)
    git(root, "remote", "remove", "origin")
    result = audit(tmp_path, head)
    assert "LOCAL_REMOTE_REPOSITORY_UNVERIFIED" in result["reasons"]
    assert result["dispatch_authorized"] is False


def test_git_ssh_origin_verifies_same_repository(tmp_path):
    root, head = prepared(tmp_path)
    git(root, "update-ref", "refs/iron-god/accepted", head)
    git(root, "remote", "set-url", "origin", "git@github.com:jweter/project-orchestrator.git")
    result = audit(tmp_path, head)
    assert result["status"] == "LOCAL_IDENTITY_VERIFIED_ONLY"
    assert result["reasons"] == []


def test_dispatch_gate_requires_engine_accepted_baseline(tmp_path):
    root, head = prepared(tmp_path)
    assert verify_activation_for_dispatch(REPO, head, activation(head), tmp_path) == (
        "ACCEPTED_REF_MISSING"
    )
    assert git(root, "rev-parse", "HEAD") == head


def test_dispatch_gate_rejects_unavailable_activation_commit(tmp_path):
    root, head = prepared(tmp_path)
    git(root, "update-ref", "refs/iron-god/accepted", head)
    result = verify_activation_for_dispatch(REPO, "b" * 40, activation("b" * 40), tmp_path)
    assert "ACTIVATION_COMMIT_NOT_LOCAL" in result


def test_dispatch_gate_allows_local_product_without_accepted_ref(tmp_path):
    root = tmp_path / "VoiceStudio"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "README.md").write_text("test", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-qm", "initial")
    git(root, "remote", "add", "origin", "git@github.com:jweter/VoiceStudio.git")
    head = git(root, "rev-parse", "HEAD")
    payload = {"heads": {"jweter/VoiceStudio": head}, "products": {
        "voicestudio": {"enabled": True, "repositories": {"jweter/VoiceStudio": head}}
    }}
    assert verify_activation_for_dispatch("jweter/VoiceStudio", head, payload, tmp_path) is None
    assert verify_activation_for_dispatch("jweter/VoiceStudio", "c" * 40, payload, tmp_path) == (
        "DISPATCH_ACTIVATION_HEAD_MISMATCH"
    )
