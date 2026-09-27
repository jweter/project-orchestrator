from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PORTFOLIO_REPOSITORIES = frozenset(
    {
        "jweter/knowledge-engine-core",
        "jweter/knowledge-engine-web",
        "jweter/knowledge-engine-ai",
        "jweter/rocksmith-cdlc-generator",
        "jweter/Project-Everward",
        "jweter/VoiceStudio",
    }
)
KNOWLEDGE_ENGINE_REPOSITORIES = frozenset(
    {
        "jweter/knowledge-engine-core",
        "jweter/knowledge-engine-web",
        "jweter/knowledge-engine-ai",
    }
)
FAILURE_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
)
NON_GREEN_TERMINAL_CONCLUSIONS = frozenset({"neutral", "skipped"})
ALLOWED_TRIGGER_EVENTS = frozenset({"check_suite", "push"})
ALLOWED_NODE_TYPES = frozenset(
    {
        "n8n-nodes-base.githubTrigger",
        "n8n-nodes-base.code",
        "n8n-nodes-base.github",
        "n8n-nodes-base.noOp",
    }
)
DISPATCH_OWNER = "jweter"
DISPATCH_REPOSITORY = "project-orchestrator"
DISPATCH_WORKFLOW = "orchestrator-observe.yml"
DISPATCH_REF = "main"
N8N_MAX_WORKFLOW_ID_LENGTH = 21
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_FORBIDDEN_CODE_TOKENS = (
    "require(",
    "process.",
    "child_process",
    "fetch(",
    "http://",
    "https://",
    "$env",
    "filesystem",
    "fs.",
)


def _locator_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("value")
        return inner if isinstance(inner, str) else ""
    return ""


def route_check_suite(payload: dict[str, Any]) -> dict[str, Any]:
    repository = payload.get("repository", {}).get("full_name", "")
    if repository not in PORTFOLIO_REPOSITORIES:
        return {"route": "IGNORE", "reason": "repository_outside_portfolio"}

    action = payload.get("action")
    if action != "completed":
        return {
            "route": "WAIT",
            "reason": "check_suite_not_completed",
            "repository": repository,
        }

    suite = payload.get("check_suite")
    if not isinstance(suite, dict):
        return {
            "route": "UNCERTAIN",
            "reason": "missing_check_suite",
            "repository": repository,
        }

    conclusion = suite.get("conclusion")
    if not isinstance(conclusion, str) or not conclusion:
        return {
            "route": "UNCERTAIN",
            "reason": "missing_conclusion",
            "repository": repository,
        }

    pull_requests = suite.get("pull_requests")
    if not isinstance(pull_requests, list):
        pull_requests = []
    pr_numbers = sorted(
        {
            item.get("number")
            for item in pull_requests
            if isinstance(item, dict) and isinstance(item.get("number"), int)
        }
    )

    envelope = {
        "repository": repository,
        "head_sha": suite.get("head_sha", ""),
        "head_branch": suite.get("head_branch", ""),
        "check_suite_id": suite.get("id"),
        "conclusion": conclusion,
        "pr_numbers": pr_numbers,
    }

    if conclusion == "success":
        return {"route": "GREEN", "reason": "check_suite_success", **envelope}

    if conclusion in FAILURE_CONCLUSIONS:
        if pr_numbers:
            return {
                "route": "REPAIR_CANDIDATE",
                "reason": "terminal_ci_failure_on_pr",
                **envelope,
            }
        return {
            "route": "OBSERVE_ONLY",
            "reason": "terminal_ci_failure_without_pr",
            **envelope,
        }

    if conclusion in NON_GREEN_TERMINAL_CONCLUSIONS:
        return {
            "route": "OBSERVE_ONLY",
            "reason": "non_green_terminal_conclusion",
            **envelope,
        }

    return {
        "route": "UNCERTAIN",
        "reason": "unknown_conclusion",
        **envelope,
    }


def route_knowledge_engine_push(payload: dict[str, Any]) -> dict[str, Any]:
    repository = payload.get("repository", {}).get("full_name", "")
    if repository not in KNOWLEDGE_ENGINE_REPOSITORIES:
        return {"route": "IGNORE", "reason": "repository_outside_knowledge_engine"}

    if payload.get("ref") != "refs/heads/main":
        return {
            "route": "IGNORE",
            "reason": "not_main_branch",
            "repository": repository,
        }

    head_sha = payload.get("after", "")
    if not isinstance(head_sha, str) or not _SHA40.fullmatch(head_sha):
        return {
            "route": "UNCERTAIN",
            "reason": "invalid_or_missing_after_sha",
            "repository": repository,
        }

    if payload.get("deleted") is True:
        return {
            "route": "UNCERTAIN",
            "reason": "main_branch_delete_event",
            "repository": repository,
            "head_sha": head_sha,
        }

    return {
        "route": "DEPENDENCY_RECHECK_CANDIDATE",
        "reason": "knowledge_engine_main_change",
        "repository": repository,
        "head_sha": head_sha,
    }


def _validate_trigger(node: dict[str, Any]) -> None:
    parameters = node.get("parameters", {})
    if parameters.get("authentication") not in {"accessToken", "oAuth2", "githubAppApi"}:
        raise ValueError(f"{node.get('name')}: unsupported GitHub trigger authentication")

    owner = _locator_value(parameters.get("owner"))
    repository = _locator_value(parameters.get("repository"))
    full_name = f"{owner}/{repository}"
    if owner != "jweter" or full_name not in PORTFOLIO_REPOSITORIES:
        raise ValueError(f"{node.get('name')}: trigger repository outside canonical portfolio")

    events = parameters.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError(f"{node.get('name')}: trigger requires explicit events")
    if not set(events).issubset(ALLOWED_TRIGGER_EVENTS):
        raise ValueError(f"{node.get('name')}: disallowed GitHub trigger event")


def _validate_code_node(node: dict[str, Any]) -> None:
    code = node.get("parameters", {}).get("jsCode", "")
    if not isinstance(code, str) or not code.strip():
        raise ValueError(f"{node.get('name')}: Code node must contain classifier code")
    lowered = code.lower()
    for token in _FORBIDDEN_CODE_TOKENS:
        if token.lower() in lowered:
            raise ValueError(f"{node.get('name')}: forbidden Code node capability: {token}")


def _validate_dispatch(node: dict[str, Any]) -> None:
    parameters = node.get("parameters", {})
    if parameters.get("resource") != "workflow" or parameters.get("operation") != "dispatch":
        raise ValueError(f"{node.get('name')}: GitHub node may only dispatch a workflow")
    if _locator_value(parameters.get("owner")) != DISPATCH_OWNER:
        raise ValueError(f"{node.get('name')}: dispatch owner must be {DISPATCH_OWNER}")
    if _locator_value(parameters.get("repository")) != DISPATCH_REPOSITORY:
        raise ValueError(f"{node.get('name')}: dispatch repository must be {DISPATCH_REPOSITORY}")
    if _locator_value(parameters.get("workflowId")) != DISPATCH_WORKFLOW:
        raise ValueError(f"{node.get('name')}: dispatch workflow must be {DISPATCH_WORKFLOW}")
    if _locator_value(parameters.get("ref")) != DISPATCH_REF:
        raise ValueError(f"{node.get('name')}: dispatch ref must be {DISPATCH_REF}")
    inputs = parameters.get("inputs")
    if not isinstance(inputs, str) or "source_repository" not in inputs or "reason" not in inputs:
        raise ValueError(f"{node.get('name')}: dispatch inputs must carry sanitized source metadata")


def validate_workflow_document(workflow: dict[str, Any]) -> None:
    workflow_id = workflow.get("id")
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise ValueError("workflow requires a stable non-empty id")
    if len(workflow_id) > N8N_MAX_WORKFLOW_ID_LENGTH:
        raise ValueError(
            "workflow id exceeds n8n static-data persistence limit "
            f"({len(workflow_id)} > {N8N_MAX_WORKFLOW_ID_LENGTH}): {workflow_id}"
        )

    if workflow.get("active") is not False:
        raise ValueError("repository-tracked n8n workflows must remain inactive until runtime review")
    if "credentials" in workflow:
        raise ValueError("workflow-level credentials must not be committed")

    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("workflow must contain nodes")

    names: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("workflow nodes must be objects")
        name = node.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("every workflow node requires a name")
        if name in names:
            raise ValueError(f"duplicate workflow node name: {name}")
        names.add(name)

        node_type = node.get("type")
        if node_type not in ALLOWED_NODE_TYPES:
            raise ValueError(f"{name}: disallowed node type: {node_type}")
        if "credentials" in node:
            raise ValueError(f"{name}: credential references must not be committed")

        if node_type == "n8n-nodes-base.githubTrigger":
            _validate_trigger(node)
        elif node_type == "n8n-nodes-base.code":
            _validate_code_node(node)
        elif node_type == "n8n-nodes-base.github":
            _validate_dispatch(node)

    connections = workflow.get("connections")
    if not isinstance(connections, dict):
        raise ValueError("workflow connections must be an object")


def validate_workflow_directory(root: Path) -> list[str]:
    manifest_path = root.parent / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("n8n workflow manifest schema_version must be 1")
    if manifest.get("scheduler") != "none":
        raise ValueError("n8n must not become a competing scheduler")
    if manifest.get("merge_authority") is not False:
        raise ValueError("n8n merge authority must remain false")

    entries = manifest.get("workflows")
    if not isinstance(entries, list) or not entries:
        raise ValueError("n8n workflow manifest must contain workflows")

    seen: set[str] = set()
    seen_workflow_ids: set[str] = set()
    validated: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("n8n workflow manifest entries must be objects")
        filename = entry.get("file")
        if not isinstance(filename, str) or not filename.endswith(".json"):
            raise ValueError("n8n workflow manifest entry requires a JSON file")
        if filename in seen:
            raise ValueError(f"duplicate n8n workflow manifest file: {filename}")
        seen.add(filename)
        if entry.get("active_in_git") is not False:
            raise ValueError(f"{filename}: active_in_git must remain false")
        if entry.get("mutation_boundary") != "orchestrator_workflow_dispatch_only":
            raise ValueError(f"{filename}: mutation boundary is too broad")

        path = root / filename
        workflow = json.loads(path.read_text(encoding="utf-8"))
        validate_workflow_document(workflow)
        workflow_id = workflow["id"]
        if workflow_id in seen_workflow_ids:
            raise ValueError(f"duplicate n8n workflow id: {workflow_id}")
        seen_workflow_ids.add(workflow_id)
        validated.append(filename)

    return validated

\n