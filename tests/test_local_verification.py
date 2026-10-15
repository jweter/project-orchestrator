"""Native exact-head evidence validation with real, isolated Git commits."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from project_orchestrator.local_git_refs import LocalGitRefs
from project_orchestrator.local_verification import (
    VerificationError,
    VerificationPolicy,
    verify_preflight,
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    ).stdout.strip()


@pytest.fixture
def case(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Iron God")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "code.py").write_text("value = 1\n", encoding="utf8")
    git(repo, "add", "code.py")
    git(repo, "commit", "-qm", "baseline")
    sha = git(repo, "rev-parse", "HEAD")
    owner = tmp_path / "flow-state"
    owner.mkdir()
    (owner / "human-mode.json").write_text('{"enabled":false}')
    evidence = tmp_path / "receipt.json"
    policy = VerificationPolicy(
        repository="jweter/VoiceStudio",
        required_checks=("format", "tests"),
        product_reality_required=False,
    )
    payload = {
        "schema_version": 3,
        "repository": "jweter/VoiceStudio",
        "head_sha": sha,
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
    evidence.write_text(json.dumps(payload), encoding="utf8")
    return repo, sha, owner, evidence, policy, payload


def verify(case, **kwargs):
    repo, sha, owner, evidence, policy, _ = case
    return verify_preflight(
        repo=repo,
        expected_sha=sha,
        evidence_path=evidence,
        policy=policy,
        owner_state_root=owner,
        **kwargs,
    )


def test_exact_green_native_receipt_validates(case):
    receipt = verify(case)
    assert receipt.head_sha == case[1]
    assert receipt.result_count == 2
    assert receipt.product_reality == "NOT_REQUIRED"
    assert len(receipt.evidence_sha256) == 64


@pytest.mark.parametrize(
    "change,diagnostic",
    [
        ({"status": "FAILED"}, "NOT_EXACT_GREEN"),
        ({"status": "RUNNING"}, "NOT_EXACT_GREEN"),
        ({"mode": "FOCUSED"}, "NOT_EXACT_GREEN"),
        ({"head_sha": "f" * 40}, "NOT_EXACT_GREEN"),
        ({"repository": "jweter/other"}, "NOT_EXACT_GREEN"),
        ({"active_check": "tests"}, "NOT_EXACT_GREEN"),
        ({"results": []}, "CHECK_SET_INCOMPLETE"),
        (
            {
                "results": [
                    {
                        "name": "tests",
                        "returncode": 0,
                        "timed_out": False,
                        "interrupted": False,
                    }
                ]
            },
            "CHECK_SET_INCOMPLETE",
        ),
    ],
)
def test_fake_or_incomplete_green_refused(case, change, diagnostic):
    payload = dict(case[5], **change)
    case[3].write_text(json.dumps(payload), encoding="utf8")
    with pytest.raises(VerificationError, match=diagnostic):
        verify(case)


def test_check_order_or_failure_blocks(case):
    result = case[5]
    result["results"][0]["name"] = "tests"
    case[3].write_text(json.dumps(result))
    with pytest.raises(VerificationError, match="CHECK_SET_MISMATCH"):
        verify(case)
    result["results"][0]["name"] = "format"
    result["results"][1]["returncode"] = 1
    case[3].write_text(json.dumps(result))
    with pytest.raises(VerificationError, match="CHECK_NOT_GREEN"):
        verify(case)


def test_dirty_tracked_worktree_is_not_exact_verified_commit(case):
    (case[0] / "code.py").write_text("value = 2\n")
    with pytest.raises(VerificationError, match="WORKTREE_DIRTY"):
        verify(case)


def test_take_wheel_and_malformed_marker_veto(case):
    for marker in ('{"enabled":true}', "invalid-json"):
        (case[2] / "human-mode.json").write_text(marker)
        with pytest.raises(VerificationError, match="OWNER_CONTROL_VETO"):
            verify(case)


def test_required_product_reality_never_faked_by_green_unit_tests(case):
    policy = VerificationPolicy(
        repository="jweter/VoiceStudio",
        required_checks=("format", "tests"),
        product_reality_required=True,
    )
    with pytest.raises(VerificationError, match="PRODUCT_REALITY_ADAPTER_REQUIRED"):
        verify_preflight(
            repo=case[0],
            expected_sha=case[1],
            evidence_path=case[3],
            policy=policy,
            owner_state_root=case[2],
        )


def test_candidate_must_have_immutable_matching_anchor(case):
    refs = LocalGitRefs(case[0], owner_state_root=case[2])
    with pytest.raises(VerificationError, match="CANDIDATE_REF_MISMATCH"):
        verify(case, candidate_refs=refs, candidate_id="slice-1")
    assert refs.protect("candidate", "slice-1", case[1])
    assert verify(case, candidate_refs=refs, candidate_id="slice-1")


def test_mismatched_head_not_accepted_even_with_green_report(case):
    (case[0] / "code.py").write_text("value = 3\n")
    git(case[0], "add", "code.py")
    git(case[0], "commit", "-qm", "changed head")
    with pytest.raises(VerificationError, match="EXACT_HEAD_MISMATCH"):
        verify(case)


def test_invalid_policy_rejected():
    with pytest.raises(VerificationError, match="DUPLICATE_POLICY_CHECK"):
        VerificationPolicy("jweter/project-orchestrator", ("tests", "tests"))
    with pytest.raises(VerificationError, match="INVALID_CHECK_POLICY"):
        VerificationPolicy("jweter/project-orchestrator", ())
