from __future__ import annotations

import copy
import urllib.parse
from collections.abc import Mapping
from typing import Any

from .github_evidence import GitHubError, GitHubNotFound, Transport


def _repo_root(repository: str) -> str:
    owner, name = repository.split("/", 1)
    return f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}"


def _context_names_from_rules(rules: list[Any]) -> tuple[set[str], list[dict[str, Any]]]:
    required: set[str] = set()
    required_workflows: list[dict[str, Any]] = []
    for raw_rule in rules:
        if not isinstance(raw_rule, Mapping):
            continue
        rule_type = raw_rule.get("type")
        parameters = raw_rule.get("parameters")
        if not isinstance(parameters, Mapping):
            parameters = {}

        if rule_type == "required_status_checks":
            raw_checks = parameters.get("required_status_checks", [])
            if isinstance(raw_checks, list):
                for item in raw_checks:
                    if isinstance(item, Mapping):
                        context = item.get("context")
                        if isinstance(context, str) and context:
                            required.add(context)

        if rule_type == "required_workflows":
            workflows = parameters.get("workflows", [])
            if isinstance(workflows, list):
                for workflow in workflows:
                    if isinstance(workflow, Mapping):
                        required_workflows.append(
                            {
                                "path": workflow.get("path"),
                                "repository_id": workflow.get("repository_id"),
                                "ref": workflow.get("ref"),
                            }
                        )

    return required, required_workflows


def _context_names_from_legacy(payload: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    contexts = payload.get("contexts")
    if isinstance(contexts, list):
        names.update(value for value in contexts if isinstance(value, str) and value)
    checks = payload.get("checks")
    if isinstance(checks, list):
        for item in checks:
            if isinstance(item, Mapping):
                context = item.get("context")
                if isinstance(context, str) and context:
                    names.add(context)
    return names


def _latest_check_by_name(check_runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for check in check_runs:
        name = check.get("name")
        if not isinstance(name, str) or not name:
            continue
        existing = latest.get(name)
        existing_id = int(existing.get("id") or 0) if existing else -1
        current_id = int(check.get("id") or 0)
        if existing is None or current_id >= existing_id:
            latest[name] = check
    return latest


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


class RequiredCheckResolver:
    """Resolve current required CI from live policy without guessing.

    Effective branch rules and legacy branch protection are authoritative.
    Control-plane mandatory check names are accepted when they are observed on
    the current PR head. A manifest-only name that is absent from the current
    head is treated as uncertainty instead of as a permanently missing check,
    because the workflow may have been renamed or retired.
    """

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def _policy_for_branch(
        self,
        repository: str,
        branch: str,
        control_plane: Mapping[str, Any],
    ) -> dict[str, Any]:
        root = _repo_root(repository)
        encoded_branch = urllib.parse.quote(branch, safe="")

        rules_state = "AVAILABLE"
        try:
            raw_rules = self.transport.get_json(
                f"{root}/rules/branches/{encoded_branch}"
            )
            if not isinstance(raw_rules, list):
                raise GitHubError(
                    f"effective branch rules are not a list for {repository}:{branch}"
                )
        except GitHubNotFound:
            rules_state = "UNAVAILABLE"
            raw_rules = []

        rule_names, required_workflows = _context_names_from_rules(raw_rules)

        legacy_state = "AVAILABLE"
        try:
            legacy = self.transport.get_json(
                f"{root}/branches/{encoded_branch}/protection/required_status_checks"
            )
            if not isinstance(legacy, Mapping):
                raise GitHubError(
                    f"legacy required status checks are not an object for "
                    f"{repository}:{branch}"
                )
            legacy_names = _context_names_from_legacy(legacy)
        except GitHubNotFound:
            legacy_state = "NONE_OR_UNAVAILABLE"
            legacy_names = set()

        manifest_state, manifest_names = _manifest_check_names(control_plane)

        return {
            "effective_rules_state": rules_state,
            "legacy_branch_protection_state": legacy_state,
            "effective_rule_check_names": sorted(rule_names),
            "legacy_check_names": sorted(legacy_names),
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

        policy_cache: dict[str, dict[str, Any]] = {}
        for pr in result.get("open_prs", []):
            base_ref = pr.get("base_ref") or result.get("default_branch")
            if not isinstance(base_ref, str) or not base_ref:
                pr["required_checks_state"] = "UNKNOWN"
                pr["required_check_policy"] = {
                    "reason": "pull request base branch is unavailable"
                }
                continue

            policy = policy_cache.get(base_ref)
            if policy is None:
                policy = self._policy_for_branch(
                    repository_name, base_ref, control_plane
                )
                policy_cache[base_ref] = policy

            observed = _latest_check_by_name(pr.get("check_runs", []))
            live_names = set(policy["effective_rule_check_names"])
            live_names.update(policy["legacy_check_names"])
            manifest_names = set(policy["manifest_check_names"])

            required: list[dict[str, Any]] = []
            missing_live: list[str] = []
            for name in sorted(live_names):
                current = observed.get(name)
                if current is None:
                    missing_live.append(name)
                    required.append(
                        {
                            "name": name,
                            "id": None,
                            "status": "missing",
                            "conclusion": None,
                            "source": "live_branch_policy",
                        }
                    )
                else:
                    required.append({**current, "source": "live_branch_policy"})

            manifest_only = manifest_names - live_names
            unconfirmed_manifest: list[str] = []
            for name in sorted(manifest_only):
                current = observed.get(name)
                if current is None:
                    unconfirmed_manifest.append(name)
                else:
                    required.append({**current, "source": "control_plane_manifest"})

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
                "missing_live_check_names": missing_live,
                "unconfirmed_manifest_check_names": unconfirmed_manifest,
            }

        return result


def resolve_required_checks(
    snapshot: dict[str, Any], transport: Transport
) -> dict[str, Any]:
    resolved = copy.deepcopy(snapshot)
    resolver = RequiredCheckResolver(transport)
    for project in resolved.get("projects", []):
        repositories = project.get("repositories", [])
        project["repositories"] = [
            resolver.resolve_repository(repository) for repository in repositories
        ]
    resolved["required_check_interpretation"] = "LIVE_POLICY_RESOLVED"
    return resolved
