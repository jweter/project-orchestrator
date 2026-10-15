"""Fail-closed, exact-head preflight receipts for Iron God Phase 3.

Git commits and repository-native preflight are evidence; green GitHub CI is a
separate release gate. This module does not grant Product Reality or publish.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_orchestrator.human_ownership import AUTHORIZED, owner_control_status
from project_orchestrator.local_git_refs import LocalGitRefs

_SHA = re.compile(r"[0-9a-f]{40}\Z")


class VerificationError(ValueError):
    """A verification or authority invariant failed. Do not promote."""


@dataclass(frozen=True)
class VerificationPolicy:
    repository: str
    required_checks: tuple[str, ...]
    product_reality_required: bool = True

    def __post_init__(self) -> None:
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*",
            self.repository,
        ):
            raise VerificationError("INVALID_REPOSITORY")
        if not self.required_checks or any(
            not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_]+", name)
            for name in self.required_checks
        ):
            raise VerificationError("INVALID_CHECK_POLICY")
        if len(set(self.required_checks)) != len(self.required_checks):
            raise VerificationError("DUPLICATE_POLICY_CHECK")


@dataclass(frozen=True)
class VerifiedPreflight:
    repository: str
    head_sha: str
    evidence_sha256: str
    result_count: int
    product_reality: str


def _git(repo: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> str:
    try:
        outcome = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationError("GIT_EXECUTION_FAILED") from exc
    if outcome.returncode not in allowed:
        raise VerificationError("GIT_CHECK_FAILED:" + args[0])
    return outcome.stdout.strip()


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_194_304:
            raise VerificationError("EVIDENCE_FILE_UNSAFE")
        raw = path.read_bytes()
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise VerificationError("EVIDENCE_NOT_OBJECT")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError("EVIDENCE_UNREADABLE") from exc
    return parsed, hashlib.sha256(raw).hexdigest()


def verify_preflight(
    *,
    repo: Path,
    expected_sha: str,
    evidence_path: Path,
    policy: VerificationPolicy,
    owner_state_root: Path,
    candidate_refs: LocalGitRefs | None = None,
    candidate_id: str | None = None,
) -> VerifiedPreflight:
    """Validate a completed native FULL preflight on an exact clean Git commit.

    Candidate verification also requires an immutable candidate ref. This
    function never creates one or trusts a pasted success claim.
    """
    if owner_control_status(owner_state_root) != AUTHORIZED:
        raise VerificationError("OWNER_CONTROL_VETO")
    if not isinstance(expected_sha, str) or not _SHA.fullmatch(expected_sha):
        raise VerificationError("INVALID_HEAD_SHA")
    repo = Path(repo)
    if _git(repo, "rev-parse", "HEAD") != expected_sha:
        raise VerificationError("EXACT_HEAD_MISMATCH")
    if _git(repo, "cat-file", "-t", expected_sha) != "commit":
        raise VerificationError("HEAD_NOT_COMMIT")
    if _git(repo, "status", "--porcelain=v1", "-uno"):
        raise VerificationError("TRACKED_WORKTREE_DIRTY")
    if (candidate_refs is None) != (candidate_id is None):
        raise VerificationError("CANDIDATE_REF_PAIR_REQUIRED")
    if candidate_refs is not None and candidate_id is not None:
        if candidate_refs.get("candidate", candidate_id) != expected_sha:
            raise VerificationError("CANDIDATE_REF_MISMATCH")
    evidence, digest = _read_json(Path(evidence_path))
    if (
        evidence.get("schema_version") != 3
        or evidence.get("repository") != policy.repository
        or evidence.get("head_sha") != expected_sha
        or evidence.get("mode") != "FULL"
        or evidence.get("status") != "GREEN"
        or evidence.get("active_check") is not None
    ):
        raise VerificationError("NOT_EXACT_GREEN_NATIVE_PREFLIGHT")
    results = evidence.get("results")
    if not isinstance(results, list) or len(results) != len(policy.required_checks):
        raise VerificationError("PREFLIGHT_CHECK_SET_INCOMPLETE")
    names = [item.get("name") if isinstance(item, dict) else None for item in results]
    if names != list(policy.required_checks):
        raise VerificationError("PREFLIGHT_CHECK_SET_MISMATCH")
    for result in results:
        if (
            result.get("returncode") != 0
            or result.get("timed_out") is not False
            or result.get("interrupted") is not False
        ):
            raise VerificationError("PREFLIGHT_CHECK_NOT_GREEN")
    if _git(repo, "rev-parse", "HEAD") != expected_sha:
        raise VerificationError("HEAD_CHANGED_DURING_EVIDENCE_READ")
    if _git(repo, "status", "--porcelain=v1", "-uno"):
        raise VerificationError("WORKTREE_CHANGED_DURING_EVIDENCE_READ")
    if owner_control_status(owner_state_root) != AUTHORIZED:
        raise VerificationError("OWNER_CONTROL_VETO")
    if policy.product_reality_required:
        # Product Reality must eventually come from the repository-specific
        # adapter (IG-080+). A unit-test receipt cannot impersonate it.
        raise VerificationError("PRODUCT_REALITY_ADAPTER_REQUIRED")
    return VerifiedPreflight(
        repository=policy.repository,
        head_sha=expected_sha,
        evidence_sha256=digest,
        result_count=len(results),
        product_reality="NOT_REQUIRED",
    )


ORCHESTRATOR_NATIVE_CHECKS = (
    "control_plane",
    "control_surface",
    "supervisor_architecture",
    "builder_architecture",
    "authority_tree",
    "portfolio_inventory",
    "autonomy_loop",
    "harness_context_audit",
    "harness_security",
    "agent_eval_suite",
    "agent_eval_runner_config",
    "specialist_lanes",
    "work_decomposition",
    "regression_prevention",
    "throughput_optimizer",
    "learning_memory",
    "learning_candidates",
    "repository_skills",
    "transient_memory",
    "compounding_timeseries",
    "tooling_capabilities",
    "n8n_policy",
    "compile",
    "tests",
    "cli_validate",
    "reinvestment_plan_smoke",
)
