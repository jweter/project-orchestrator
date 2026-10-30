"""Native IG-044 read-only provenance checks against real disposable Git + SQLite."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from project_orchestrator.local_acceptance import (
    commit_or_recover_prepared_acceptance,
    prepare_verified_acceptance,
)
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs
from project_orchestrator.local_verification import VerificationPolicy
from project_orchestrator.publication_provenance_audit import (
    PublicationProvenanceError,
    inspect_milestone,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.strip()


@pytest.fixture
def verified(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "IG Test")
    git(repo, "config", "user.email", "ig@example.invalid")
    (repo / "code.py").write_text("n=1\n", encoding="utf8")
    git(repo, "add", "code.py")
    git(repo, "commit", "-qm", "baseline")
    base = git(repo, "rev-parse", "HEAD")
    flow = tmp_path / "flow"
    flow.mkdir()
    marker = flow / "human-mode.json"
    marker.write_text('{"enabled":false}', encoding="utf8")
    refs = LocalGitRefs(repo, owner_state_root=flow)
    refs.bootstrap_accepted(base)
    (repo / "code.py").write_text("n=2\n", encoding="utf8")
    git(repo, "add", "code.py")
    git(repo, "commit", "-qm", "candidate")
    target = git(repo, "rev-parse", "HEAD")
    payload = {
        "schema_version": 3,
        "repository": "jweter/Test",
        "head_sha": target,
        "mode": "FULL",
        "status": "GREEN",
        "active_check": None,
        "results": [
            {
                "name": "format",
                "returncode": 0,
                "timed_out": False,
                "interrupted": False,
            },
            {
                "name": "tests",
                "returncode": 0,
                "timed_out": False,
                "interrupted": False,
            },
        ],
    }
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf8")
    policy = VerificationPolicy(
        repository="jweter/Test",
        required_checks=("format", "tests"),
        product_reality_required=False,
    )
    state = LocalFirstState(tmp_path / "state.db")
    prepare_verified_acceptance(
        repo=repo,
        state=state,
        refs=refs,
        milestone_id="m1",
        accepted_base_sha=base,
        target_sha=target,
        evidence_path=evidence,
        policy=policy,
    )
    commit_or_recover_prepared_acceptance(
        repo=repo,
        state=state,
        refs=refs,
        milestone_id="m1",
        evidence_path=evidence,
        policy=policy,
    )
    return {
        "repo": repo,
        "state": state,
        "refs": refs,
        "milestone_id": "m1",
        "evidence_path": evidence,
        "policy": policy,
        "marker": marker,
        "target": target,
        "base": base,
    }


def inspect(c: dict, **kw):
    return inspect_milestone(
        repo=c["repo"],
        state=c["state"],
        refs=c["refs"],
        milestone_id=c["milestone_id"],
        evidence_path=c["evidence_path"],
        policy=c["policy"],
        **kw,
    )


def test_genuine_acceptance_is_inspectable(verified):
    result = inspect(verified)
    assert result.accepted_sha == verified["target"]
    assert result.milestone_id == "m1"


def test_untrusted_direct_milestone_has_no_committed_intent(verified):
    c = verified
    state = c["state"]
    state.record_accepted_milestone(
        repository="jweter/Test",
        milestone_id="forged",
        accepted_sha=c["target"],
        verification_sha256=hashlib.sha256(c["evidence_path"].read_bytes()).hexdigest(),
        product_reality="NOT_REQUIRED",
    )
    c["milestone_id"] = "forged"
    with pytest.raises(PublicationProvenanceError, match="MILESTONE_RECORD_AMBIGUOUS"):
        inspect(c)


def test_owner_veto_prevents_inspection(verified):
    c = verified
    c["marker"].write_text('{"enabled":true}', encoding="utf8")
    with pytest.raises(PublicationProvenanceError, match="OWNER_CONTROL_VETO"):
        inspect(c)


def test_missing_protected_milestone_ref_blocks(verified):
    c = verified
    git(c["repo"], "update-ref", "-d", "refs/iron-god/milestone/m1")
    with pytest.raises(PublicationProvenanceError, match="MILESTONE_REF_UNVERIFIED"):
        inspect(c)


def test_receipt_bytes_drift_is_rejected(verified):
    c = verified
    payload = json.loads(c["evidence_path"].read_text())
    c["evidence_path"].write_text(json.dumps(payload, indent=3))
    with pytest.raises(
        PublicationProvenanceError, match="ACCEPTANCE_RECEIPT_IDENTITY_CHANGED"
    ):
        inspect(c)


def test_tracked_dirty_worktree_blocks(verified):
    c = verified
    (c["repo"] / "code.py").write_text("n=3\n", encoding="utf8")
    with pytest.raises(PublicationProvenanceError, match="NATIVE_RECEIPT_UNVERIFIED"):
        inspect(c)


def test_sqlite_intent_commit_mismatch_blocks(verified):
    c = verified
    with sqlite3.connect(c["state"].path) as db:
        db.execute(
            "UPDATE acceptance_intents SET verification_sha256=? WHERE milestone_id='m1'",
            ("a" * 64,),
        )
    with pytest.raises(PublicationProvenanceError, match="UNTRUSTED_ACCEPTANCE_INTENT"):
        inspect(c)


def test_product_reality_cannot_be_implied(verified):
    c = verified
    c["policy"] = VerificationPolicy(
        repository="jweter/Test",
        required_checks=("format", "tests"),
        product_reality_required=True,
    )
    with pytest.raises(PublicationProvenanceError, match="NATIVE_RECEIPT_UNVERIFIED"):
        inspect(c)


def test_accepted_ref_absent_blocks(verified):
    c = verified
    git(c["repo"], "update-ref", "-d", "refs/iron-god/accepted")
    with pytest.raises(
        PublicationProvenanceError, match="ACCEPTED_REF_ANCESTRY_UNVERIFIED"
    ):
        inspect(c)
