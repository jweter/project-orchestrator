"""Free-only Unreal adapter: no live Unreal, paid API, or owner control bypass."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_orchestrator.iron_god_unreal_agent_stack import (
    execute_authorized, make_native_request,
)
from project_orchestrator.product_reality_dispatch import dispatch_plan


def job():
    return {
        "repository": "jweter/Project-Everward",
        "purpose": "product_reality_evidence_collection",
        "base_sha": "a" * 40,
        "merge_authority": False,
    }


def workspace(tmp_path):
    root = tmp_path / "everward"
    script = root / "tools/unreal_free_agent_stack.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('installed')", encoding="utf-8")
    python = tmp_path / "python.exe"
    python.write_text("python", encoding="utf-8")
    return root, python


def test_job_plan_is_free_and_not_automatically_authorized():
    debt = {
        "project": "everward", "repository": "jweter/Project-Everward",
        "kind": "unreal_playtest", "state": "PENDING",
        "summary": "native Unreal tests", "dependency_scope": "playtest",
        "source_ref": "issue:900",
    }
    result = dispatch_plan(debt, branch="main", base_sha="a" * 40)
    assert result["job"]["free_native_suite"] == "automation"
    assert result["job"]["free_native_execution_authorized"] is False
    assert result["job"]["merge_authority"] is False


def test_native_request_is_scoped(tmp_path):
    root, python = workspace(tmp_path)
    request = make_native_request(
        job(), checkout=root, python_exe=python, engine=tmp_path / "UE",
        evidence_dir=tmp_path / "reports", suite="automation", test="Everward.Mining")
    assert request.cwd == root
    assert "--ack-isolated-worktree" in request.argv
    assert "run-native" in request.argv
    assert all("aura" not in part.lower() for part in request.argv)


@pytest.mark.parametrize("suite", ["aura", "cloud", "mcp-write", "unknown"])
def test_forbids_paid_or_unapproved_suite(tmp_path, suite):
    root, python = workspace(tmp_path)
    with pytest.raises(ValueError, match="UNREVIEWED_NATIVE_SUITE"):
        make_native_request(job(), checkout=root, python_exe=python,
                            engine=tmp_path / "UE", evidence_dir=tmp_path / "reports",
                            suite=suite)


def test_forbids_arbitrary_console_commands(tmp_path):
    root, python = workspace(tmp_path)
    with pytest.raises(ValueError, match="INVALID_NATIVE_TEST_NAME"):
        make_native_request(job(), checkout=root, python_exe=python,
                            engine=tmp_path / "UE", evidence_dir=tmp_path / "reports",
                            test="Everward; Quit; shell")


def test_rejects_wrong_product_or_purpose(tmp_path):
    root, python = workspace(tmp_path)
    with pytest.raises(ValueError, match="ONLY_EVERWARD"):
        make_native_request({**job(), "repository": "example/other"}, checkout=root,
                            python_exe=python, engine=tmp_path / "UE",
                            evidence_dir=tmp_path / "reports")


def test_live_authority_callback_required(tmp_path):
    root, python = workspace(tmp_path)
    with pytest.raises(ValueError, match="LIVE_AUTHORITY_CALLBACK_REQUIRED"):
        execute_authorized(job(), checkout=root, python_exe=python,
                           engine=tmp_path / "UE", evidence_dir=tmp_path / "reports",
                           authority_check=None, runtime=object())


def test_revoke_before_launch_stops_runtime(tmp_path):
    root, python = workspace(tmp_path)
    class Runtime:
        def execute_supervised(self, *_):
            raise AssertionError("must not be called")
    def revoked():
        raise RuntimeError("owner revoked")
    with pytest.raises(RuntimeError, match="owner revoked"):
        execute_authorized(job(), checkout=root, python_exe=python,
                           engine=tmp_path / "UE", evidence_dir=tmp_path / "reports",
                           authority_check=revoked, runtime=Runtime())


def test_supervised_runtime_runs_but_cannot_claim_product_reality(tmp_path):
    root, python = workspace(tmp_path)
    calls = []
    class Runtime:
        def execute_supervised(self, request, check):
            check()
            calls.append(request)
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "head": "a" * 40, "status": "REVIEW_REQUIRED",
                "native_process_returncode": 0,
                "product_reality_verified": True,
                "merge_authority": True,
            }))
    def guard():
        calls.append("guard")
    result = execute_authorized(
        job(), checkout=root, python_exe=python, engine=tmp_path / "UE",
        evidence_dir=tmp_path / "reports", authority_check=guard, runtime=Runtime())
    assert result["merge_authority"] is False
    assert result["product_reality_verified"] is False
    assert len(calls) == 4


def test_mismatch_exacts_head_never_accepted(tmp_path):
    root, python = workspace(tmp_path)
    class Runtime:
        def execute_supervised(self, *_):
            return SimpleNamespace(returncode=0,
                                   stdout=json.dumps({"head": "b"*40, "status":"REVIEW_REQUIRED"}))
    result = execute_authorized(job(), checkout=root, python_exe=python,
                                engine=tmp_path / "UE", evidence_dir=tmp_path / "reports",
                                authority_check=lambda: None, runtime=Runtime())
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["reason"] == "EXACT_HEAD_MISMATCH"
