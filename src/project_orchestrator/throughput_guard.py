from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
from typing import Any

from .repair import fingerprint_failure


PROMOTION_FAILURE_CLASSES = {
    "CODE_FAILURE",
    "TEST_FAILURE",
    "QUALITY_FAILURE",
    "REVIEW_FAILURE",
    "PLATFORM_GATE",
    "PRODUCT_REALITY_PENDING",
    "POLICY_FAILURE",
    "ENVIRONMENT_FAILURE",
    "UNKNOWN",
}
ACTIVE_LEASE_STATES = {"IMPLEMENTING", "REPAIRING"}
LEASE_STATES = ACTIVE_LEASE_STATES | {"RELEASED"}
LEDGER_STATES = {
    "IMPLEMENTING",
    "PREFLIGHT_UNVERIFIED",
    "PREFLIGHT_GREEN",
    "PR_OPEN",
    "CI_PENDING",
    "GREEN",
    "MERGED",
    "PRODUCT_REALITY_PENDING",
    "PRODUCT_REALITY_VERIFIED",
    "PARKED_FOR_RETRY",
    "FAILED",
}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_CODE_FAILURE_RE = re.compile(
    r"(syntaxerror|nameerror|attributeerror|compilation terminated|compiler error|undefined reference)",
    re.I,
)


def _require_nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _require_sha(value: Any, name: str = "head_sha") -> str:
    text = _require_nonempty_string(value, name)
    if not _SHA_RE.fullmatch(text):
        raise ValueError(f"{name} must be a 40-character lowercase hex SHA")
    return text


def _content_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def build_fast_gate_plan(
    *,
    repository: str,
    control_plane: dict[str, Any],
    changed_paths: list[str],
) -> dict[str, Any]:
    """Select only repository-authorized cheap checks before canonical preflight.

    The guard never invents commands. Repositories opt in under ``fast_gate`` and
    every executable argv must be declared there. Full preflight remains mandatory.
    """
    repository = _require_nonempty_string(repository, "repository")
    if not isinstance(changed_paths, list) or not changed_paths:
        raise ValueError("changed_paths must be a non-empty list")
    normalized_paths = [_require_nonempty_string(path, "changed_path") for path in changed_paths]

    policy = control_plane.get("fast_gate")
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        return {
            "schema_version": 1,
            "repository": repository,
            "status": "NOT_AUTHORIZED",
            "changed_paths": normalized_paths,
            "commands": [],
            "canonical_preflight_still_required": True,
        }

    raw_commands = policy.get("commands")
    if not isinstance(raw_commands, list):
        raise ValueError("fast_gate.commands must be a list")

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for item in raw_commands:
        if not isinstance(item, dict):
            raise ValueError("fast_gate command entries must be objects")
        command_id = _require_nonempty_string(item.get("id"), "fast_gate command id")
        argv = item.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(part, str) and part for part in argv):
            raise ValueError(f"fast_gate command {command_id} argv must be a non-empty string list")
        extensions = item.get("extensions", [])
        prefixes = item.get("path_prefixes", [])
        always = item.get("always", False)
        if not isinstance(extensions, list) or not all(isinstance(ext, str) for ext in extensions):
            raise ValueError(f"fast_gate command {command_id} extensions must be strings")
        if not isinstance(prefixes, list) or not all(isinstance(prefix, str) for prefix in prefixes):
            raise ValueError(f"fast_gate command {command_id} path_prefixes must be strings")
        if not isinstance(always, bool):
            raise ValueError(f"fast_gate command {command_id} always must be boolean")

        matches_extension = any(path.endswith(tuple(extensions)) for path in normalized_paths) if extensions else False
        matches_prefix = any(path.startswith(tuple(prefixes)) for path in normalized_paths) if prefixes else False
        if not (always or matches_extension or matches_prefix):
            continue
        key = tuple(argv)
        if key in seen:
            continue
        seen.add(key)
        selected.append({"id": command_id, "argv": list(argv)})

    return {
        "schema_version": 1,
        "repository": repository,
        "status": "READY" if selected else "NO_MATCHING_COMMANDS",
        "changed_paths": normalized_paths,
        "commands": selected,
        "canonical_preflight_still_required": True,
    }


def execute_fast_gate(plan: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    """Execute a READY fast-gate plan in a local checkout; never commit or push."""
    if plan.get("status") != "READY":
        return {**plan, "execution_status": "NOT_EXECUTED", "executions": []}

    executions: list[dict[str, Any]] = []
    for item in plan.get("commands", []):
        completed = subprocess.run(
            item["argv"],
            cwd=repo_root,
            text=True,
            capture_output=True,
        )
        executions.append(
            {
                "id": item["id"],
                "argv": item["argv"],
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
        )
        if completed.returncode != 0:
            return {**plan, "execution_status": "FAILED", "executions": executions}
    return {**plan, "execution_status": "GREEN", "executions": executions}


def classify_promotion_failure(evidence: dict[str, Any]) -> dict[str, Any]:
    """Classify a promotion blocker without asserting a root cause beyond evidence."""
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")

    if evidence.get("product_reality_pending") is True:
        failure_class = "PRODUCT_REALITY_PENDING"
        basis = "explicit Product Reality debt"
    elif evidence.get("review_state") == "CHANGES_REQUESTED" or evidence.get("blocking_comments") is True:
        failure_class = "REVIEW_FAILURE"
        basis = "blocking review/comment evidence"
    elif (
        evidence.get("workflow_conclusion") in {"action_required", "cancelled", "startup_failure"}
        and evidence.get("jobs_count") == 0
    ):
        failure_class = "PLATFORM_GATE"
        basis = "workflow terminated before job instantiation"
    elif evidence.get("policy_failure") is True:
        failure_class = "POLICY_FAILURE"
        basis = "explicit security/provenance/governance policy failure"
    else:
        log_text = evidence.get("log_text", "")
        if not isinstance(log_text, str):
            raise ValueError("log_text must be a string")
        if _CODE_FAILURE_RE.search(log_text):
            failure_class = "CODE_FAILURE"
            basis = "compiler/runtime code error signature"
        else:
            fingerprint = fingerprint_failure(log_text)
            mapped = {
                "FORMAT": "QUALITY_FAILURE",
                "IMPORT_SORT": "QUALITY_FAILURE",
                "LINT_SAFE_FIX": "QUALITY_FAILURE",
                "TYPE_CHECK": "QUALITY_FAILURE",
                "TEST_FAILURE": "TEST_FAILURE",
                "SECURITY": "POLICY_FAILURE",
                "GOVERNANCE": "POLICY_FAILURE",
                "PROVENANCE": "POLICY_FAILURE",
                "PRODUCT_REALITY": "PRODUCT_REALITY_PENDING",
                "ENVIRONMENT": "ENVIRONMENT_FAILURE",
            }
            failure_class = mapped.get(fingerprint.failure_class, "UNKNOWN")
            basis = (
                f"repair fingerprint {fingerprint.failure_class}"
                if fingerprint.failure_class != "UNKNOWN"
                else "insufficient deterministic evidence"
            )

    return {
        "schema_version": 1,
        "failure_class": failure_class,
        "basis": basis,
        "fail_closed": failure_class == "UNKNOWN",
    }


def build_active_work_lease(
    *,
    repository: str,
    branch: str,
    head_sha: str,
    actor: str,
    state: str,
    acquired_at: str,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    """Build an ephemeral exact-head lease used only to prevent merge races."""
    repository = _require_nonempty_string(repository, "repository")
    branch = _require_nonempty_string(branch, "branch")
    actor = _require_nonempty_string(actor, "actor")
    head_sha = _require_sha(head_sha)
    acquired_at = _require_nonempty_string(acquired_at, "acquired_at")
    if state not in ACTIVE_LEASE_STATES:
        raise ValueError(f"active lease state must be one of {sorted(ACTIVE_LEASE_STATES)}")
    normalized_paths = [] if paths is None else [_require_nonempty_string(path, "path") for path in paths]
    payload = {
        "repository": repository,
        "branch": branch,
        "head_sha": head_sha,
        "actor": actor,
        "state": state,
        "acquired_at": acquired_at,
        "paths": sorted(set(normalized_paths)),
    }
    return {"schema_version": 1, "lease_id": _content_id(payload), **payload}


def release_active_work_lease(lease: dict[str, Any], *, released_at: str) -> dict[str, Any]:
    released_at = _require_nonempty_string(released_at, "released_at")
    if lease.get("state") not in ACTIVE_LEASE_STATES:
        raise ValueError("only an active lease can be released")
    return {**lease, "state": "RELEASED", "released_at": released_at}


def evaluate_merge_lease_gate(
    *,
    repository: str,
    branch: str,
    pr_head_sha: str,
    leases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Block merge while the exact PR branch is actively IMPLEMENTING/REPAIRING."""
    repository = _require_nonempty_string(repository, "repository")
    branch = _require_nonempty_string(branch, "branch")
    pr_head_sha = _require_sha(pr_head_sha, "pr_head_sha")
    if not isinstance(leases, list):
        raise ValueError("leases must be a list")

    for lease in leases:
        if not isinstance(lease, dict):
            raise ValueError("lease entries must be objects")
        if lease.get("repository") != repository or lease.get("branch") != branch:
            continue
        state = lease.get("state")
        if state not in LEASE_STATES:
            raise ValueError("lease state is invalid")
        if state == "RELEASED":
            continue
        lease_head = _require_sha(lease.get("head_sha"), "lease.head_sha")
        if lease_head == pr_head_sha:
            return {
                "schema_version": 1,
                "merge_allowed": False,
                "state": "BLOCKED_ACTIVE_LEASE",
                "lease_id": lease.get("lease_id"),
                "reason": "exact PR head is actively being implemented or repaired",
            }
        return {
            "schema_version": 1,
            "merge_allowed": False,
            "state": "STALE_LEASE_REQUIRES_REVALIDATION",
            "lease_id": lease.get("lease_id"),
            "reason": "active lease refers to the same branch at a different head SHA",
        }

    return {
        "schema_version": 1,
        "merge_allowed": True,
        "state": "LEASE_CLEAR",
        "lease_id": None,
        "reason": "no active lease blocks this exact branch/head",
    }


def build_issue_ledger_event(
    *,
    issue_number: int,
    promotion_state: str,
    next_gate: str,
    event_at: str,
    branch: str | None = None,
    head_sha: str | None = None,
    verification: str | None = None,
    failure_class: str | None = None,
    product_reality: str | None = None,
) -> dict[str, Any]:
    if isinstance(issue_number, bool) or not isinstance(issue_number, int) or issue_number < 1:
        raise ValueError("issue_number must be a positive integer")
    if promotion_state not in LEDGER_STATES:
        raise ValueError("promotion_state is invalid")
    next_gate = _require_nonempty_string(next_gate, "next_gate")
    event_at = _require_nonempty_string(event_at, "event_at")
    if head_sha is not None:
        head_sha = _require_sha(head_sha)
    if failure_class is not None and failure_class not in PROMOTION_FAILURE_CLASSES:
        raise ValueError("failure_class is invalid")

    payload = {
        "issue_number": issue_number,
        "promotion_state": promotion_state,
        "branch": branch,
        "head_sha": head_sha,
        "verification": verification,
        "failure_class": failure_class,
        "product_reality": product_reality,
        "next_gate": next_gate,
        "event_at": event_at,
        "actor": "ORCHESTRATOR",
    }
    return {"schema_version": 1, "event_id": _content_id(payload), **payload}


def render_issue_ledger_comment(event: dict[str, Any]) -> str:
    lines = [
        "### DoWork engineering state",
        f"- **State:** `{event['promotion_state']}`",
        f"- **Actor:** `{event.get('actor', 'UNKNOWN')}`",
    ]
    if event.get("branch"):
        lines.append(f"- **Branch:** `{event['branch']}`")
    if event.get("head_sha"):
        lines.append(f"- **Exact head:** `{event['head_sha']}`")
    if event.get("verification"):
        lines.append(f"- **Verification:** {event['verification']}")
    if event.get("failure_class"):
        lines.append(f"- **Failure class:** `{event['failure_class']}`")
    if event.get("product_reality"):
        lines.append(f"- **Product Reality:** {event['product_reality']}")
    lines.append(f"- **Next gate:** {event['next_gate']}")
    lines.append(f"- **Observed at:** `{event['event_at']}`")
    lines.append(f"- **Ledger event:** `{event['event_id']}`")
    return "\n".join(lines)


def _parse_time(value: Any) -> datetime:
    text = _require_nonempty_string(value, "timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed


def _metric(value: float | int | None, unit: str, sample_count: int, reason: str) -> dict[str, Any]:
    if value is None:
        return {"status": "UNKNOWN", "value": None, "unit": unit, "sample_count": 0, "reason": reason}
    return {"status": "MEASURED", "value": value, "unit": unit, "sample_count": sample_count, "reason": ""}


def compute_rework_kpis(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute rework KPIs only from explicitly supplied evidence records."""
    if not isinstance(records, list):
        raise ValueError("records must be a list")

    preflight_attempts: list[int] = []
    merge_races = 0
    merge_race_samples = 0
    merged_samples = 0
    post_merge_hotfixes = 0
    cycle_seconds: list[float] = []

    for record in records:
        if not isinstance(record, dict):
            raise ValueError("record entries must be objects")
        attempts = record.get("preflight_attempts")
        if attempts is not None:
            if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
                raise ValueError("preflight_attempts must be a positive integer")
            preflight_attempts.append(attempts)

        if "merge_race" in record:
            if not isinstance(record["merge_race"], bool):
                raise ValueError("merge_race must be boolean")
            merge_race_samples += 1
            merge_races += int(record["merge_race"])

        merged = record.get("merged")
        if merged is not None and not isinstance(merged, bool):
            raise ValueError("merged must be boolean")
        if merged is True:
            merged_samples += 1
            hotfix = record.get("post_merge_correctness_hotfix")
            if not isinstance(hotfix, bool):
                raise ValueError("merged records require post_merge_correctness_hotfix boolean")
            post_merge_hotfixes += int(hotfix)
            if record.get("implementation_started_at") and record.get("merged_at"):
                start = _parse_time(record["implementation_started_at"])
                end = _parse_time(record["merged_at"])
                delta = (end - start).total_seconds()
                if delta < 0:
                    raise ValueError("merged_at cannot precede implementation_started_at")
                cycle_seconds.append(delta)

    first_pass = None
    if preflight_attempts:
        first_pass = sum(1 for attempts in preflight_attempts if attempts == 1) / len(preflight_attempts)
    hotfix_rate = None if merged_samples == 0 else post_merge_hotfixes / merged_samples
    median_cycle = None if not cycle_seconds else statistics.median(cycle_seconds)

    return {
        "schema_version": 1,
        "first_pass_preflight_rate": _metric(
            first_pass,
            "ratio",
            len(preflight_attempts),
            "no explicit preflight-attempt evidence",
        ),
        "merge_race_count": _metric(
            merge_races if merge_race_samples else None,
            "count",
            merge_race_samples,
            "no explicit merge-race evidence",
        ),
        "post_merge_correctness_hotfix_rate": _metric(
            hotfix_rate,
            "ratio",
            merged_samples,
            "no merged-record hotfix evidence",
        ),
        "median_implementation_to_merge_seconds": _metric(
            median_cycle,
            "seconds",
            len(cycle_seconds),
            "no records contain both implementation and merge timestamps",
        ),
    }
