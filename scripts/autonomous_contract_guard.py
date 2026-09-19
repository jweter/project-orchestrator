"""Fail-closed semantic guard for autonomous pre-PR changes."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

POLICY_CONTRACTS = {
    "engineering/agent-action-guard.json",
    "engineering/control-plane.json",
    "engineering/emerging-compute-policy.json",
}
SET_PRESERVE_FIELDS = {
    "states",
    "required_context",
    "existing_gates_preserved",
}
EXACT_PRESERVE_FIELDS = {
    "execution_authority",
    "failure_or_retry_additional_requirement",
    "paid_external_service_rule",
    "reserved_human_rule",
    "hard_cap_rule",
}
KNOWN_POLICY_WEAKENING_FINGERPRINT = "AUTONOMOUS_POLICY_CONTRACT_WEAKENING_174"
def _norm(path: str) -> str:
    return path.replace("\\", "/").strip("/").lower()


def _git_show_json(workspace: Path, base_sha: str, path: str) -> dict[str, Any] | None:
    proc = subprocess.run(
        ["git", "show", f"{base_sha}:{path}"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _candidate_json(workspace: Path, path: str) -> dict[str, Any] | None:
    candidate = workspace / path
    if not candidate.is_file():
        return None
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
def _missing_keys(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    return sorted(set(baseline) - set(candidate))


def assess_policy_contract(path: str, baseline: dict[str, Any], candidate: dict[str, Any]) -> str | None:
    missing = _missing_keys(baseline, candidate)
    if missing:
        return f"{KNOWN_POLICY_WEAKENING_FINGERPRINT}:deleted_keys:{','.join(missing)}"

    for field in SET_PRESERVE_FIELDS:
        before = baseline.get(field)
        after = candidate.get(field)
        if isinstance(before, list):
            if not isinstance(after, list) or not set(before).issubset(set(after)):
                return f"{KNOWN_POLICY_WEAKENING_FINGERPRINT}:reduced_set:{field}"

    for field in EXACT_PRESERVE_FIELDS:
        if field in baseline and candidate.get(field) != baseline.get(field):
            return f"{KNOWN_POLICY_WEAKENING_FINGERPRINT}:changed_contract:{field}"

    if "default_policy" in baseline and candidate.get("default_policy") != baseline.get("default_policy"):
        return f"{KNOWN_POLICY_WEAKENING_FINGERPRINT}:changed_default_policy"

    return None


def contract_guard_failure(workspace: Path, base_sha: str, paths: list[str]) -> str | None:
    for raw in paths:
        path = _norm(raw)
        if path not in POLICY_CONTRACTS:
            continue
        baseline = _git_show_json(workspace, base_sha, raw)
        candidate = _candidate_json(workspace, raw)
        if baseline is None or candidate is None:
            return f"{KNOWN_POLICY_WEAKENING_FINGERPRINT}:contract_unreadable:{path}"
        failure = assess_policy_contract(path, baseline, candidate)
        if failure:
            return failure
    return None
