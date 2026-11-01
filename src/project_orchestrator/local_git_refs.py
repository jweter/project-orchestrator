"""Protected local Git checkpoints for Iron God's local-first development.

Git is the source of truth for commit bytes. SQLite stores lifecycle state,
and neither a database row nor a bare SHA proves that Git still retains work.
This module only creates refs and enforces compare-and-swap; it does NOT grant
integration, milestone acceptance, or publication authority.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from project_orchestrator.human_ownership import AUTHORIZED, owner_control_status

_ZERO = "0" * 40
_SHA = re.compile(r"[a-f0-9]{40}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_KINDS = frozenset({"candidate", "milestone", "recovery", "publication"})
# The cumulative accepted baseline. It used to live at refs/iron-god/accepted, which Git cannot hold beside the product
# lane's per-packet anchors refs/iron-god/accepted/<packet> (a ref and a directory of the same name, #597). New writes go
# to BASELINE_REF; the legacy name is only ever READ, as a fallback, and is never written or deleted.
BASELINE_REF = "refs/iron-god/baseline/accepted"
LEGACY_BASELINE_REF = "refs/iron-god/accepted"


class LocalGitRefError(ValueError):
    """Fail closed on invalid or conflicting authority/evidence."""


def _id(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise LocalGitRefError("INVALID_REF_IDENTIFIER")
    if value.endswith(".lock") or value.endswith(".") or ".." in value:
        raise LocalGitRefError("INVALID_REF_IDENTIFIER")
    return value


def _sha(value: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise LocalGitRefError("INVALID_SHA")
    return value


class LocalGitRefs:
    """Ref protection under the existing repository's own object database.

    The required owner_state_root must be the real central flow-state directory.
    All mutations check it and no method changes owner authority. Owner gate
    is rechecked immediately before each compare-and-swap invocation.
    """

    def __init__(self, repo: Path, *, owner_state_root: Path) -> None:
        self.repo = Path(repo)
        self.owner_state_root = Path(owner_state_root)
        if not self.repo.is_dir():
            raise LocalGitRefError("REPOSITORY_NOT_FOUND")
        result = self._run("rev-parse", "--is-inside-work-tree")
        if result.stdout.strip() != "true":
            raise LocalGitRefError("NOT_GIT_WORKTREE")

    def _run(self, *args: str, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
        try:
            p = subprocess.run(
                ["git", "-C", str(self.repo), *args],
                capture_output=True, text=True, check=False, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalGitRefError("GIT_EXECUTION_UNAVAILABLE") from exc
        if p.returncode not in allowed:
            raise LocalGitRefError(
                f"GIT_COMMAND_FAILED:{args[0]}:{p.returncode}:"
                f"{p.stderr.strip()[-180:]}"
            )
        return p

    def _owner_check(self) -> None:
        if owner_control_status(self.owner_state_root) != AUTHORIZED:
            raise LocalGitRefError("OWNER_CONTROL_VETO")

    @staticmethod
    def _ref(kind: str, identifier: str) -> str:
        if kind not in _KINDS:
            raise LocalGitRefError("INVALID_REF_CLASS")
        return f"refs/iron-god/{kind}/{_id(identifier)}"

    def _commit_exists(self, sha: str) -> None:
        _sha(sha)
        if self._run("cat-file", "-t", sha, allowed=(0, 1, 128)).stdout.strip() != "commit":
            raise LocalGitRefError("COMMIT_NOT_FOUND")

    def get(self, kind: str, identifier: str) -> str | None:
        ref = self._ref(kind, identifier)
        p = self._run("rev-parse", "--verify", "--quiet", ref, allowed=(0, 1))
        return p.stdout.strip() if p.returncode == 0 else None

    def protect(self, kind: str, identifier: str, commit: str) -> bool:
        """Create immutable anchor once; same SHA retry is idempotent."""
        ref = self._ref(kind, identifier)
        self._owner_check()
        self._commit_exists(commit)
        current = self.get(kind, identifier)
        if current is not None:
            if current != commit:
                raise LocalGitRefError("PROTECTED_REF_CONFLICT")
            return False
        self._owner_check()
        try:
            self._run("update-ref", "--create-reflog", ref, commit, _ZERO)
        except LocalGitRefError as exc:
            # Race-safe: a concurrent identical insertion is harmless.
            if self.get(kind, identifier) == commit:
                return False
            raise LocalGitRefError("PROTECTED_REF_RACE") from exc
        return True

    def _read(self, ref: str) -> str | None:
        p = self._run("rev-parse", "--verify", "--quiet", ref, allowed=(0, 1))
        return p.stdout.strip() if p.returncode == 0 else None

    def legacy_baseline(self) -> str | None:
        """The pre-#597 baseline at LEGACY_BASELINE_REF, if the repository still has one (read-only, never written)."""
        return self._read(LEGACY_BASELINE_REF)

    def accepted(self) -> str | None:
        """The cumulative accepted baseline: the current ref, else the legacy one so older repositories keep working."""
        return self._read(BASELINE_REF) or self._read(LEGACY_BASELINE_REF)

    def bootstrap_accepted(self, commit: str) -> bool:
        """Seed accepted baseline ONLY after caller verifies release authority.

        This does not itself perform tests or confirm that the commit is released.
        """
        self._owner_check()
        self._commit_exists(commit)
        current = self.accepted()
        if current is not None:
            if current != commit:
                raise LocalGitRefError("ACCEPTED_ALREADY_INITIALIZED")
            return False
        self._owner_check()
        try:
            self._run(
                "update-ref", "--create-reflog",
                BASELINE_REF, commit, _ZERO,
            )
        except LocalGitRefError as exc:
            if self.accepted() == commit:
                return False
            raise LocalGitRefError("ACCEPTED_BOOTSTRAP_RACE") from exc
        return True

    def advance_accepted(self, *, expected_sha: str, new_sha: str) -> None:
        """CAS-only fast-forward; caller must supply verified integration receipt."""
        self._owner_check()
        _sha(expected_sha)
        self._commit_exists(new_sha)
        if expected_sha == new_sha:
            raise LocalGitRefError("NO_ACCEPTED_ADVANCEMENT")
        # Capture what is stored at the NEW name once, before validating: that value is the compare-and-swap's expected old
        # value. Re-reading it at write time would let a stale caller overwrite an acceptance that landed in between.
        stored = self._read(BASELINE_REF)
        current = stored or self._read(LEGACY_BASELINE_REF)
        if current != expected_sha:
            raise LocalGitRefError("ACCEPTED_BASE_CHANGED")
        ff = self._run(
            "merge-base", "--is-ancestor", expected_sha, new_sha,
            allowed=(0, 1),
        )
        if ff.returncode != 0:
            raise LocalGitRefError("NON_FAST_FORWARD_ACCEPTANCE")
        self._owner_check()
        try:
            # A repository that only has the legacy baseline starts the new ref from nothing (old value zero), so a
            # concurrent creation of the new ref fails the swap; the legacy ref is left untouched.
            self._run(
                "update-ref", "--create-reflog",
                BASELINE_REF, new_sha, stored or _ZERO,
            )
        except LocalGitRefError as exc:
            raise LocalGitRefError("ACCEPTED_COMPARE_AND_SWAP_FAILED") from exc
