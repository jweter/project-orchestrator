from __future__ import annotations

import copy
import urllib.parse
from collections.abc import Mapping
from typing import Any

from .github_evidence import GitHubError, GitHubNotFound, Transport


def _repo_root(repository: str) -> str:
    owner, name = repository.split("/", 1)
    return f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}"


def _normalize_app_id(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _dedupe_requirements(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int | None]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = (item["name"], item.get("app_id"))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _requirements_from_rules(rules: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    required: list[dict[str, Any]] = []
    required_workflows: list[dict[str, Any]] = []
    for raw_rule in rules:
        if not isinstance(raw_rule, Mapping):
            continue
        parameters = raw_rule.get("parameters")
        if not isinstance(parameters, Mapping):
            parameters = {}
        if raw_rule.get("type") == "required_status_checks":
            raw_checks = parameters.get("required_status_checks", [])
            if isinstance(raw_checks, list):
                for item in raw_checks:
                    if isinstance(item, Mapping):
                        context = item.get("context")
                        if isinstance(context, str) and context:
                            required.append({"name": context, "app_id": _normalize_app_id(item.get("integration_id"))})
        if raw_rule.get("type") == "required_workflows":
            workflows = parameters.get("workflows", [])
            if isinstance(workflows, list):
                for workflow in workflows:
                    if isinstance(workflow, Mapping):
                        required_workflows.append({"path": workflow.get("path"), "repository_id": workflow.get("repository_id"), "ref": workflow.get("ref")})
    return _dedupe_requirements(required), required_workflows


def _requirements_from_legacy(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    required: list[dict[str, Any]] = []
    contexts = payload.get("contexts")
    if isinstance(contexts, list):
        for value in contexts:
            if isinstance(value, str) and value:
                required.append({"name": value, "app_id": None})
    checks = payload.get("checks")
    if isinstance(checks, list):
        for item in checks:
            if isinstance(item, Mapping):
                context = item.get("context")
                if isinstance(context, str) and context:
                    required.append({"name": context, "app_id": _normalize_app_id(item.get("app_id"))})
    return _dedupe_requirements(required)


def _manifest_check_names(control_plane: Mapping[str, Any]) -> tuple[str, set[str]]:
    if not control_plane.get("present"):
        return "ABSENT", set()
    if control_plane.get("parse_state") != "PARSED":
        return "UNAVAILABLE", set()
    verification = control_plane.get("verification")
    if not isinstance(verification, Mapping):
        return "UNSPECIFIED", set()
    names = verification.get("mandatory_check_names")
    if names is None:
        return "UNSPECIFIED", set()
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        return "MALFORMED", set()
    return "DECLARED", {name for name in names if name}


def _sort_key(evidence: Mapping[str, Any]) -> tuple[str, int]:
    timestamp = str(evidence.get("completed_at") or evidence.get("started_at") or "")
    return timestamp, int(evidence.get("id") or 0)


def _select_observed(pr: Mapping[str, Any], name: str, app_id: int | None) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for check in pr.get("check_runs", []):
        if not isinstance(check, Mapping) or check.get("name") != name:
            continue
        if app_id is not None and check.get("app_id") != app_id:
            continue
        candidates.append(dict(check))
    if app_id is None:
        for status in pr.get("status_contexts", []):
            if isinstance(status, Mapping) and status.get("name") == name:
                candidates.append(dict(status))
    return max(candidates, key=_sort_key) if candidates else None


class RequiredCheckResolver:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def _policy_for_branch(self, repository: str, branch: str, control_plane: Mapping[str, Any]) -> dict[str, Any]:
        root = _repo_root(repository)
        encoded_branch = urllib.parse.quote(branch, safe="")

        try:
            raw_rules = self.transport.get_json(f"{root}/rules/branches/{encoded_branch}")
            if not isinstance(raw_rules, list):
                raise GitHubError(f"effective branch rules are not a list for {repository}:{branch}")
            rules_state = "AVAILABLE"
        except GitHubNotFound:
            raw_rules, rules_state = [], "UNAVAILABLE"

        rule_requirements, required_workflows = _requirements_from_rules(raw_rules)

        try:
            legacy = self.transport.get_json(f"{root}/branches/{encoded_branch}/protection/required_status_checks")
            if not isinstance(legacy, Mapping):
                raise GitHubError(f"legacy required status checks are not an object for {repository}:{branch}")
            legacy_state = "AVAILABLE"
            legacy_requirements = _requirements_from_legacy(legacy)
        except GitHubNotFound:
            legacy_state, legacy_requirements = "NONE_OR_UNAVAILABLE", []

        manifest_state, manifest_names = _manifest_check_names(control_plane)
        live_requirements = _dedupe_requirements([*rule_requirements, *legacy_requirements])
        return {
            "effective_rules_state": rules_state,
            "legacy_branch_protection_state": legacy_state,
            "live_requirements": live_requirements,
            "manifest_state": manifest_state,
            "manifest_check_names": sorted(manifest_names),
            "required_workflows": required_workflows,
        }

    def resolve_repository(self, repository: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(repository)
        repository_name = result["name"]
        control_plane = result.get("control_plane")
        if not isinstance(control_plane, Mapping):
            control_plane = {}

        cache: dict[str, dict[str, Any]] = {}
        for pr in result.get("open_prs", []):
            base_ref = pr.get("base_ref") or result.get("default_branch")
            if not isinstance(base_ref, str) or not base_ref:
                pr["required_checks_state"] = "UNKNOWN"
                pr["required_check_policy"] = {"reason": "pull request base branch is unavailable"}
                continue

            policy = cache.get(base_ref)
            if policy is None:
                policy = self._policy_for_branch(repository_name, base_ref, control_plane)
                cache[base_ref] = policy

            live_requirements = policy["live_requirements"]
            live_names = {item["name"] for item in live_requirements}
            manifest_names = set(policy["manifest_check_names"])
            required: list[dict[str, Any]] = []
            missing_live: list[dict[str, Any]] = []

            for requirement in live_requirements:
                observed = _select_observed(pr, requirement["name"], requirement.get("app_id"))
                if observed is None:
                    missing_live.append(requirement)
                    required.append({
                        "name": requirement["name"],
                        "id": None,
                        "status": "missing",
                        "conclusion": None,
                        "app_id": None,
                        "required_app_id": requirement.get("app_id"),
                        "source": "live_branch_policy",
                    })
                else:
                    required.append({
                        **observed,
                        "required_app_id": requirement.get("app_id"),
                        "source": "live_branch_policy",
                    })

            unconfirmed_manifest: list[str] = []
            for name in sorted(manifest_names - live_names):
                observed = _select_observed(pr, name, None)
                if observed is None:
                    unconfirmed_manifest.append(name)
                else:
                    required.append({**observed, "required_app_id": None, "source": "control_plane_manifest"})

            if missing_live:
                state = "MISSING"
            elif policy["required_workflows"]:
                state = "UNCERTAIN"
            elif unconfirmed_manifest:
                state = "UNCERTAIN"
            elif required:
                state = "RESOLVED"
            elif (
                policy["effective_rules_state"] == "AVAILABLE"
                and policy["manifest_state"] == "DECLARED"
                and not manifest_names
            ):
                state = "CONFIRMED_NONE"
            else:
                state = "UNKNOWN"

            pr["required_checks"] = required
            pr["required_checks_state"] = state
            pr["required_check_policy"] = {
                **policy,
                "missing_live_checks": missing_live,
                "unconfirmed_manifest_check_names": unconfirmed_manifest,
            }
        return result


def resolve_required_checks(snapshot: dict[str, Any], transport: Transport) -> dict[str, Any]:
    resolved = copy.deepcopy(snapshot)
    resolver = RequiredCheckResolver(transport)
    for project in resolved.get("projects", []):
        project["repositories"] = [resolver.resolve_repository(repo) for repo in project.get("repositories", [])]
    resolved["required_check_interpretation"] = "LIVE_POLICY_RESOLVED"
    return resolved
