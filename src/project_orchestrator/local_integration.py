"""IG-032/033: isolated, conflict-detecting local Git integration staging.

Staging never updates refs/iron-god/accepted, SQLite, or GitHub. The integration
commit is a recoverable *candidate* until repository-native FULL preflight
and Product Reality gates accept it in a separate controlled transaction.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from project_orchestrator.human_ownership import AUTHORIZED, owner_control_status
from project_orchestrator.local_git_refs import LocalGitRefs

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,110}\Z")


class IntegrationError(ValueError):
    """A failed gate leaves protected source candidates and accepted ref intact."""


@dataclass(frozen=True)
class IntegrationStage:
    candidate_sha: str
    accepted_base_sha: str
    integrated_sha: str
    worktree: Path
    recovery_ref: str


class LocalIntegrationController:
    """Stage a deterministic local merge, without claiming milestone acceptance.

    The caller owns a dedicated integration directory outside the repository.
    Existing workspaces are never overwritten, removed, or reset.
    """

    def __init__(
        self,
        repo: Path,
        *,
        workspace_root: Path,
        owner_state_root: Path,
    ) -> None:
        self.repo = Path(repo).resolve()
        self.workspace_root = Path(workspace_root).absolute()
        self.owner_state_root = Path(owner_state_root)
        self.refs = LocalGitRefs(self.repo, owner_state_root=self.owner_state_root)
        if self.workspace_root == self.repo or self.repo in self.workspace_root.parents:
            raise IntegrationError("INTEGRATION_ROOT_INSIDE_REPOSITORY")
        if self.workspace_root.is_symlink():
            raise IntegrationError("INTEGRATION_ROOT_SYMLINK")
        if self.workspace_root.exists() and not self.workspace_root.is_dir():
            raise IntegrationError("INTEGRATION_ROOT_NOT_DIRECTORY")

    def _owner(self) -> None:
        if owner_control_status(self.owner_state_root) != AUTHORIZED:
            raise IntegrationError("OWNER_CONTROL_VETO")

    def _git(
        self,
        *args: str,
        cwd: Path | None = None,
        allowed: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["git", "-C", str(cwd or self.repo), *args],
                check=False,
                text=True,
                capture_output=True,
                timeout=90,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise IntegrationError("GIT_EXECUTION_FAILED") from exc
        if result.returncode not in allowed:
            raise IntegrationError(f"GIT_{args[0].upper()}_FAILED:{result.returncode}")
        return result

    @staticmethod
    def _sha(value: str) -> str:
        if not isinstance(value, str) or not _SHA.fullmatch(value):
            raise IntegrationError("INVALID_BASELINE_SHA")
        return value

    @staticmethod
    def _id(value: str) -> str:
        if (
            not isinstance(value, str)
            or not _ID.fullmatch(value)
            or value.endswith((".", ".lock"))
            or ".." in value
        ):
            raise IntegrationError("INVALID_INTEGRATION_ID")
        return value

    def stage(
        self,
        *,
        integration_id: str,
        candidate_id: str,
        candidate_base_sha: str,
        expected_accepted_sha: str,
    ) -> IntegrationStage:
        """Stage isolated merge and protect its exact commit for later verification.

        This method intentionally does not run tests or promote accepted.
        A conflict or owner veto fails closed; an interrupted created worktree
        is retained for explicit reconciliation instead of automatic deletion.
        """
        ident = self._id(integration_id)
        self._sha(candidate_base_sha)
        self._sha(expected_accepted_sha)
        self._owner()
        candidate = self.refs.get("candidate", candidate_id)
        if candidate is None:
            raise IntegrationError("UNPROTECTED_CANDIDATE")
        if self.refs.accepted() != expected_accepted_sha:
            raise IntegrationError("ACCEPTED_BASE_CHANGED")
        if self._git("cat-file", "-t", candidate).stdout.strip() != "commit":
            raise IntegrationError("CANDIDATE_NOT_COMMIT")
        if (
            self._git(
                "cat-file",
                "-t",
                candidate_base_sha,
                allowed=(0, 1, 128),
            ).stdout.strip()
            != "commit"
        ):
            raise IntegrationError("CANDIDATE_BASE_NOT_SHARED")
        for ancestor, child in (
            (candidate_base_sha, candidate),
            (candidate_base_sha, expected_accepted_sha),
        ):
            ancestry = self._git(
                "merge-base",
                "--is-ancestor",
                ancestor,
                child,
                allowed=(0, 1),
            )
            if ancestry.returncode:
                raise IntegrationError("CANDIDATE_BASE_NOT_SHARED")
        if (
            candidate == expected_accepted_sha
            or self._git(
                "merge-base",
                "--is-ancestor",
                candidate,
                expected_accepted_sha,
                allowed=(0, 1),
            ).returncode
            == 0
        ):
            raise IntegrationError("CANDIDATE_ALREADY_INTEGRATED")
        recovery_id = f"integrated-{ident}"
        if self.refs.get("recovery", recovery_id) is not None:
            raise IntegrationError("INTEGRATION_ID_ALREADY_PROTECTED")
        target = self.workspace_root / ident
        if target.exists() or target.is_symlink():
            raise IntegrationError("INTEGRATION_WORKTREE_OCCUPIED")
        # git merge-tree computes real Git conflicts without modifying source
        # worktree, accepted ref, or user branches.
        self._owner()
        probe = self._git(
            "merge-tree",
            "--write-tree",
            expected_accepted_sha,
            candidate,
            allowed=(0, 1),
        )
        if probe.returncode:
            raise IntegrationError("INTEGRATION_CONFLICT")
        self._owner()
        if self.refs.accepted() != expected_accepted_sha:
            raise IntegrationError("ACCEPTED_BASE_CHANGED")
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._owner()
        self._git(
            "worktree",
            "add",
            "--detach",
            "--",
            str(target),
            expected_accepted_sha,
        )
        # Preserve a partially built worktree on failure; never use reset/clean.
        self._owner()
        self._git(
            "-c",
            "user.name=Iron God Integration",
            "-c",
            "user.email=iron-god@local.invalid",
            "merge",
            "--no-ff",
            "--no-edit",
            candidate,
            cwd=target,
        )
        integrated = self._git("rev-parse", "HEAD", cwd=target).stdout.strip()
        if (
            integrated == expected_accepted_sha
            or self._git("status", "--porcelain=v1", "-uno", cwd=target).stdout.strip()
        ):
            raise IntegrationError("INTEGRATED_WORKTREE_NOT_CLEAN")
        if self.refs.accepted() != expected_accepted_sha:
            raise IntegrationError("ACCEPTED_BASE_CHANGED_AFTER_MERGE")
        self._owner()
        self.refs.protect("recovery", recovery_id, integrated)
        return IntegrationStage(
            candidate_sha=candidate,
            accepted_base_sha=expected_accepted_sha,
            integrated_sha=integrated,
            worktree=target,
            recovery_ref=f"refs/iron-god/recovery/{recovery_id}",
        )
