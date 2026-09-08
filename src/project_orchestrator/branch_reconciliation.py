from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any, Protocol

_REQUIRED = {"repository", "default_branch", "default_head_sha", "branch", "branch_head_sha", "ahead_by", "behind_by", "changed_files"}


def _non_negative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _validate_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) < 7:
        raise ValueError(f"{field} must be a commit SHA")
    return value


def build_reconciliation_plan(evidence: dict[str, Any]) -> dict[str, Any]:
    """Classify existing branch value and emit a fail-closed modernization plan.

    This function is deliberately decision-only. It never rewrites refs, rebases,
    cherry-picks, deletes branches, opens PRs, or claims verification.
    """
    missing = _REQUIRED - evidence.keys()
    if missing:
        raise ValueError("branch evidence missing fields: " + ", ".join(sorted(missing)))

    repository = evidence["repository"]
    default_branch = evidence["default_branch"]
    branch = evidence["branch"]
    if not all(isinstance(x, str) and x for x in (repository, default_branch, branch)):
        raise ValueError("repository, default_branch, and branch must be non-empty strings")
    if branch == default_branch:
        raise ValueError("reconciliation candidate cannot be the default branch")

    default_head = _validate_sha(evidence["default_head_sha"], "default_head_sha")
    branch_head = _validate_sha(evidence["branch_head_sha"], "branch_head_sha")
    ahead = _non_negative_int(evidence["ahead_by"], "ahead_by")
    behind = _non_negative_int(evidence["behind_by"], "behind_by")

    changed_files = evidence["changed_files"]
    if not isinstance(changed_files, list) or not all(isinstance(p, str) and p for p in changed_files):
        raise ValueError("changed_files must be a list of non-empty paths")

    open_pr = evidence.get("open_pr")
    actively_owned = bool(evidence.get("actively_owned", False))
    roadmap_authorized = evidence.get("roadmap_authorized")
    if roadmap_authorized not in {True, False, None}:
        raise ValueError("roadmap_authorized must be true, false, or null")

    if ahead == 0 and behind == 0:
        state = "ALIGNED_NO_UNIQUE_WORK"
        action = "NO_RECONCILIATION"
    elif ahead == 0 and behind > 0:
        state = "STALE_NO_UNIQUE_WORK"
        action = "BRANCH_HYGIENE_REVIEW"
    elif ahead > 0 and behind == 0:
        state = "CURRENT_UNMERGED"
        action = "VERIFY_AND_PROMOTE"
    else:
        state = "DIVERGED_VALUABLE"
        action = "MODERNIZE_ON_CURRENT_DEFAULT"

    blockers: list[str] = []
    if open_pr:
        blockers.append("branch already has an open PR; continue that PR lane instead of creating competing work")
    if actively_owned:
        blockers.append("branch has active ownership; do not take over without ownership clearance")
    if roadmap_authorized is False:
        blockers.append("unique work is no longer authorized by current repository roadmap")
    if roadmap_authorized is None and ahead > 0:
        blockers.append("roadmap authorization is unverified")
    if evidence.get("product_reality_dependency") and not evidence.get("product_reality_verified", False):
        blockers.append("candidate depends on unverified Product Reality")

    promotable = action == "VERIFY_AND_PROMOTE" and not blockers
    modernization_ready = action == "MODERNIZE_ON_CURRENT_DEFAULT" and not blockers

    steps: list[str] = []
    if modernization_ready:
        steps = [
            f"create a fresh modernization branch from exact default head {default_head}",
            "replay only still-authorized unique changes from the stale branch without rewriting published history",
            "inspect changed paths against current architecture, regression memory, ownership, and compatibility rules",
            "run repository-approved formatters and targeted tests",
            "run full exact-head preflight",
            "open a Ready-for-Review PR only after PREFLIGHT_GREEN",
            "require fresh independent PR CI before merge",
        ]
    elif promotable:
        steps = [
            "inspect regression memory and repository authority for the changed area",
            "run repository-approved formatters and targeted tests",
            "run full exact-head preflight on the existing branch",
            "open or continue a Ready-for-Review PR only after PREFLIGHT_GREEN",
            "require fresh independent PR CI before merge",
        ]
    elif action == "BRANCH_HYGIENE_REVIEW":
        steps = [
            "verify no open PR, active ownership, release/hotfix role, or unique unmerged commit remains",
            "delete only if branch-hygiene safety checks prove it disposable",
        ]

    return {
        "schema_version": 1,
        "repository": repository,
        "default_branch": default_branch,
        "default_head_sha": default_head,
        "branch": branch,
        "branch_head_sha": branch_head,
        "ahead_by": ahead,
        "behind_by": behind,
        "changed_files": sorted(set(changed_files)),
        "state": state,
        "recommended_action": action,
        "blockers": blockers,
        "modernization_ready": modernization_ready,
        "promotable": promotable,
        "steps": steps,
        "promotion_state": "IMPLEMENTING" if modernization_ready else "PREFLIGHT_UNVERIFIED" if promotable else "BLOCKED" if blockers else "NOT_APPLICABLE",
        "safety_boundaries": [
            "never rewrite published history merely to manufacture a PR",
            "never delete a branch with uncertain disposition",
            "never assume stale unique work is still roadmap-authorized",
            "never claim preflight or CI success without exact-head evidence",
            "existing open PR ownership outranks branch modernization",
            "Product Reality blocks only work that depends on that result",
        ],
    }


class CommandRunner(Protocol):
    def __call__(self, command: list[str], cwd: Path) -> "CommandResult": ...


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _subprocess_runner(command: list[str], cwd: Path) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _command_list(value: Any, field: str) -> list[list[str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of argv lists")
    commands: list[list[str]] = []
    for command in value:
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command
        ):
            raise ValueError(f"{field} must contain non-empty argv lists")
        commands.append(command)
    return commands


def execute_reconciliation(
    evidence: dict[str, Any],
    *,
    repo_root: Path,
    modernization_branch: str,
    unique_commits: list[str],
    format_commands: list[list[str]] | None = None,
    targeted_test_commands: list[list[str]] | None = None,
    preflight_command: list[str] | None = None,
    runner: CommandRunner = _subprocess_runner,
) -> dict[str, Any]:
    """Modernize authorized stale work onto exact current default, fail closed.

    Execution is intentionally local and non-destructive: it creates a new
    branch, never rewrites the stale source branch, never force-pushes, never
    deletes refs, and never opens or merges a PR. Promotion remains gated on
    exact-head GREEN preflight plus fresh independent PR CI.
    """
    plan = build_reconciliation_plan(evidence)
    if not plan["modernization_ready"]:
        raise ValueError("branch is not authorized and ready for modernization")
    if not isinstance(repo_root, Path):
        repo_root = Path(repo_root)
    if not modernization_branch or modernization_branch in {
        plan["branch"],
        plan["default_branch"],
    }:
        raise ValueError("modernization_branch must be a new non-default branch")
    if not unique_commits:
        raise ValueError("unique_commits must contain the still-authorized commits to replay")
    for sha in unique_commits:
        _validate_sha(sha, "unique_commit")
    formats = _command_list(format_commands, "format_commands")
    targeted = _command_list(targeted_test_commands, "targeted_test_commands")
    preflight = preflight_command or ["python", "engineering/preflight.py"]
    if not isinstance(preflight, list) or not preflight or not all(
        isinstance(part, str) and part for part in preflight
    ):
        raise ValueError("preflight_command must be a non-empty argv list")

    transcript: list[dict[str, Any]] = []

    def run(command: list[str], stage: str) -> CommandResult:
        result = runner(command, repo_root)
        transcript.append(
            {
                "stage": stage,
                "command": command,
                "returncode": result.returncode,
            }
        )
        return result

    clean = run(["git", "status", "--porcelain"], "clean_worktree")
    if clean.returncode != 0 or clean.stdout.strip():
        return _execution_result(plan, modernization_branch, transcript, "BLOCKED", "working tree is not clean")

    live_default = run(
        ["git", "rev-parse", plan["default_branch"]], "verify_default_head"
    )
    if live_default.returncode != 0 or live_default.stdout.strip() != plan["default_head_sha"]:
        return _execution_result(
            plan,
            modernization_branch,
            transcript,
            "STALE",
            "live default head no longer matches reconciliation evidence",
        )

    created = run(
        ["git", "switch", "-c", modernization_branch, plan["default_head_sha"]],
        "create_modernization_branch",
    )
    if created.returncode != 0:
        return _execution_result(plan, modernization_branch, transcript, "FAILED", "could not create modernization branch")

    for sha in unique_commits:
        replay = run(["git", "cherry-pick", sha], "replay_authorized_commit")
        if replay.returncode != 0:
            run(["git", "cherry-pick", "--abort"], "abort_conflicted_replay")
            return _execution_result(
                plan,
                modernization_branch,
                transcript,
                "CONFLICTED",
                f"authorized commit {sha} did not replay cleanly",
            )

    for command in formats:
        result = run(command, "format")
        if result.returncode != 0:
            return _execution_result(plan, modernization_branch, transcript, "FAILED", "repository-approved formatting failed")

    for command in targeted:
        result = run(command, "targeted_test")
        if result.returncode != 0:
            return _execution_result(plan, modernization_branch, transcript, "FAILED", "targeted verification failed")

    verified = run(preflight, "full_preflight")
    if verified.returncode != 0:
        return _execution_result(plan, modernization_branch, transcript, "PREFLIGHT_FAILED", "full preflight failed")

    head = run(["git", "rev-parse", "HEAD"], "capture_exact_head")
    if head.returncode != 0 or not head.stdout.strip():
        return _execution_result(plan, modernization_branch, transcript, "FAILED", "could not capture exact verified head")

    result = _execution_result(plan, modernization_branch, transcript, "PREFLIGHT_GREEN", None)
    result["verified_head_sha"] = head.stdout.strip()
    result["next_action"] = "PUSH_BRANCH_THEN_OPEN_READY_PR_AFTER_REMOTE_EXACT_HEAD_REVALIDATION"
    return result


def _execution_result(
    plan: dict[str, Any],
    modernization_branch: str,
    transcript: list[dict[str, Any]],
    state: str,
    blocker: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "repository": plan["repository"],
        "source_branch": plan["branch"],
        "source_head_sha": plan["branch_head_sha"],
        "default_head_sha": plan["default_head_sha"],
        "modernization_branch": modernization_branch,
        "execution_state": state,
        "blocker": blocker,
        "transcript": transcript,
        "source_branch_mutated": False,
        "destructive_action_performed": False,
        "pr_created": False,
        "merge_performed": False,
        "safety_boundary": "local modernization only; push/PR/merge/deletion require their normal independent gates",
    }
