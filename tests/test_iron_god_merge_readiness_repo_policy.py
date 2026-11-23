"""Repository-scoped preflight cannot be dropped by Iron God's merge-readiness adapter."""

from pathlib import Path

import pytest

from project_orchestrator import iron_god_merge_readiness as readiness

ROOT = Path(__file__).resolve().parents[1]
HEAD = "a" * 40


def _pr(checks: list[dict]) -> dict:
    return {
        "headRefOid": HEAD, "baseRefName": "main", "state": "OPEN",
        "mergeStateStatus": "CLEAN", "isDraft": False,
        "author": {"login": "jweter"}, "reviewDecision": "",
        "statusCheckRollup": checks,
    }


def _ci() -> dict:
    return {"name": "test", "workflowName": "CI",
            "status": "COMPLETED", "conclusion": "SUCCESS"}


def _preflight(status: str, conclusion: str) -> dict:
    return {"name": "full-preflight", "workflowName": "Preflight",
            "status": status, "conclusion": conclusion}


def _assess(repository: str, checks: list[dict]) -> readiness.Readiness:
    return readiness.assess(
        _pr(checks), [], repository=repository, verified_head=HEAD,
        verification_ok=True, settled=True, age_seconds=9999,
        repaired_thread_ids=set(), merger=readiness.load_merger(ROOT),
    )


@pytest.mark.parametrize("checks, status, reason", [
    ([_ci()], readiness.BLOCKED, "FULL_PREFLIGHT_CHECK_MISSING"),
    ([_ci(), _preflight("IN_PROGRESS", "")], readiness.WAIT, "FULL_PREFLIGHT_CHECK_PENDING"),
    ([_ci(), _preflight("COMPLETED", "FAILURE")], readiness.BLOCKED, "FULL_PREFLIGHT_NOT_GREEN"),
    ([_ci(), _preflight("COMPLETED", "SKIPPED")], readiness.BLOCKED, "FULL_PREFLIGHT_NOT_GREEN"),
    ([_ci(), _preflight("COMPLETED", "SUCCESS")], readiness.MERGE, "GREEN"),
])
def test_orchestrator_requires_current_full_preflight(checks, status, reason):
    result = _assess("jweter/project-orchestrator", checks)
    assert (result.status, result.reason) == (status, reason)


def test_other_portfolio_repo_does_not_inherit_orchestrator_only_preflight():
    result = _assess("jweter/VoiceStudio", [_ci()])
    assert (result.status, result.reason) == (readiness.MERGE, "GREEN")
