from pathlib import Path

import pytest

from project_orchestrator.branch_reconciliation import (
    CommandResult,
    build_reconciliation_plan,
    execute_reconciliation,
)


def evidence(**overrides):
    base = {
        "repository": "jweter/Project-Everward",
        "default_branch": "main",
        "default_head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "branch": "orchestrator/fix-it-repair-kernel",
        "branch_head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "ahead_by": 23,
        "behind_by": 34,
        "changed_files": ["src/simulation/fix_it.hpp", "tests/fix_it_tests.cpp"],
        "roadmap_authorized": True,
    }
    base.update(overrides)
    return base


def test_diverged_authorized_branch_is_modernization_candidate():
    plan = build_reconciliation_plan(evidence())
    assert plan["state"] == "DIVERGED_VALUABLE"
    assert plan["modernization_ready"] is True
    assert plan["recommended_action"] == "MODERNIZE_ON_CURRENT_DEFAULT"
    assert plan["promotion_state"] == "IMPLEMENTING"
    assert "fresh modernization branch" in plan["steps"][0]


def test_unknown_roadmap_authority_fails_closed():
    plan = build_reconciliation_plan(evidence(roadmap_authorized=None))
    assert plan["modernization_ready"] is False
    assert plan["promotion_state"] == "BLOCKED"
    assert "roadmap authorization is unverified" in plan["blockers"]


def test_existing_open_pr_prevents_competing_modernization():
    plan = build_reconciliation_plan(evidence(open_pr=491))
    assert plan["modernization_ready"] is False
    assert any("open PR" in reason for reason in plan["blockers"])


def test_current_unique_branch_can_move_to_verification():
    plan = build_reconciliation_plan(evidence(ahead_by=2, behind_by=0))
    assert plan["state"] == "CURRENT_UNMERGED"
    assert plan["promotable"] is True
    assert plan["promotion_state"] == "PREFLIGHT_UNVERIFIED"


def test_stale_branch_without_unique_work_is_hygiene_review_only():
    plan = build_reconciliation_plan(evidence(ahead_by=0, behind_by=12, roadmap_authorized=None))
    assert plan["state"] == "STALE_NO_UNIQUE_WORK"
    assert plan["recommended_action"] == "BRANCH_HYGIENE_REVIEW"
    assert plan["promotion_state"] == "NOT_APPLICABLE"


def test_default_branch_is_never_a_candidate():
    with pytest.raises(ValueError, match="default branch"):
        build_reconciliation_plan(evidence(branch="main"))


def test_negative_divergence_is_rejected():
    with pytest.raises(ValueError, match="behind_by"):
        build_reconciliation_plan(evidence(behind_by=-1))


def test_product_reality_blocks_only_dependent_candidate():
    plan = build_reconciliation_plan(evidence(product_reality_dependency=True, product_reality_verified=False))
    assert plan["modernization_ready"] is False
    assert any("Product Reality" in reason for reason in plan["blockers"])



class FakeRunner:
    def __init__(self, responses=None):
        self.commands = []
        self.responses = responses or {}

    def __call__(self, command, cwd):
        self.commands.append((command, cwd))
        key = tuple(command)
        if key in self.responses:
            return self.responses[key]
        if command == ["git", "status", "--porcelain"]:
            return CommandResult(0, "")
        if command == ["git", "rev-parse", "main"]:
            return CommandResult(0, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        if command == ["git", "rev-parse", "HEAD"]:
            return CommandResult(0, "cccccccccccccccccccccccccccccccccccccccc\n")
        return CommandResult(0, "")


def test_executor_replays_authorized_commits_and_requires_preflight_green():
    runner = FakeRunner()
    result = execute_reconciliation(
        evidence(),
        repo_root=Path("/repo"),
        modernization_branch="orchestrator/recovered-449",
        unique_commits=["1234567890abcdef"],
        format_commands=[["ruff", "format", "."]],
        targeted_test_commands=[["pytest", "tests/test_dose.py"]],
        preflight_command=["python", "engineering/preflight.py"],
        runner=runner,
    )
    assert result["execution_state"] == "PREFLIGHT_GREEN"
    assert result["verified_head_sha"] == "cccccccccccccccccccccccccccccccccccccccc"
    commands = [command for command, _ in runner.commands]
    assert ["git", "switch", "-c", "orchestrator/recovered-449", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"] in commands
    assert ["git", "cherry-pick", "1234567890abcdef"] in commands
    assert ["python", "engineering/preflight.py"] in commands
    assert result["source_branch_mutated"] is False
    assert result["pr_created"] is False


def test_executor_stops_if_default_head_changed():
    runner = FakeRunner({
        ("git", "rev-parse", "main"): CommandResult(0, "dddddddddddddddddddddddddddddddddddddddd\n")
    })
    result = execute_reconciliation(
        evidence(),
        repo_root=Path("/repo"),
        modernization_branch="orchestrator/recovered-449",
        unique_commits=["1234567890abcdef"],
        runner=runner,
    )
    assert result["execution_state"] == "STALE"
    assert not any(command[:3] == ["git", "switch", "-c"] for command, _ in runner.commands)


def test_executor_aborts_conflicted_cherry_pick_without_touching_source_branch():
    runner = FakeRunner({
        ("git", "cherry-pick", "1234567890abcdef"): CommandResult(1, "", "conflict")
    })
    result = execute_reconciliation(
        evidence(),
        repo_root=Path("/repo"),
        modernization_branch="orchestrator/recovered-449",
        unique_commits=["1234567890abcdef"],
        runner=runner,
    )
    assert result["execution_state"] == "CONFLICTED"
    assert (["git", "cherry-pick", "--abort"], Path("/repo")) in runner.commands
    assert result["source_branch_mutated"] is False


def test_executor_fails_closed_on_dirty_worktree():
    runner = FakeRunner({
        ("git", "status", "--porcelain"): CommandResult(0, " M local.txt\n")
    })
    result = execute_reconciliation(
        evidence(),
        repo_root=Path("/repo"),
        modernization_branch="orchestrator/recovered-449",
        unique_commits=["1234567890abcdef"],
        runner=runner,
    )
    assert result["execution_state"] == "BLOCKED"
    assert len(runner.commands) == 1


def test_executor_never_claims_green_when_preflight_fails():
    runner = FakeRunner({
        ("python", "engineering/preflight.py"): CommandResult(1, "", "failed")
    })
    result = execute_reconciliation(
        evidence(),
        repo_root=Path("/repo"),
        modernization_branch="orchestrator/recovered-449",
        unique_commits=["1234567890abcdef"],
        preflight_command=["python", "engineering/preflight.py"],
        runner=runner,
    )
    assert result["execution_state"] == "PREFLIGHT_FAILED"
    assert "verified_head_sha" not in result


def test_executor_rejects_reusing_source_or_default_branch_name():
    for branch in ("main", "orchestrator/fix-it-repair-kernel"):
        with pytest.raises(ValueError, match="modernization_branch"):
            execute_reconciliation(
                evidence(),
                repo_root=Path("/repo"),
                modernization_branch=branch,
                unique_commits=["1234567890abcdef"],
                runner=FakeRunner(),
            )
