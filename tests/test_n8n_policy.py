from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_orchestrator.n8n_policy import (
    route_check_suite,
    route_knowledge_engine_push,
    validate_workflow_directory,
    validate_workflow_document,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "n8n" / "workflows"


def _check_suite(conclusion: str = "failure", prs: list[dict] | None = None) -> dict:
    return {
        "action": "completed",
        "repository": {"full_name": "jweter/knowledge-engine-core"},
        "check_suite": {
            "id": 123,
            "head_sha": "a" * 40,
            "head_branch": "agent/fix",
            "conclusion": conclusion,
            "pull_requests": [{"number": 42}] if prs is None else prs,
        },
    }


def test_failed_pr_check_suite_becomes_repair_candidate() -> None:
    result = route_check_suite(_check_suite())
    assert result["route"] == "REPAIR_CANDIDATE"
    assert result["pr_numbers"] == [42]
    assert result["head_sha"] == "a" * 40


def test_success_is_green_but_not_a_merge_authorization() -> None:
    result = route_check_suite(_check_suite("success"))
    assert result["route"] == "GREEN"
    assert "merge" not in result


def test_failure_without_pr_is_observe_only() -> None:
    result = route_check_suite(_check_suite(prs=[]))
    assert result["route"] == "OBSERVE_ONLY"


def test_unknown_repository_is_ignored() -> None:
    payload = _check_suite()
    payload["repository"]["full_name"] = "jweter/unmanaged"
    assert route_check_suite(payload)["route"] == "IGNORE"


def test_missing_conclusion_fails_closed() -> None:
    payload = _check_suite()
    payload["check_suite"]["conclusion"] = None
    assert route_check_suite(payload)["route"] == "UNCERTAIN"


def test_neutral_is_not_green() -> None:
    assert route_check_suite(_check_suite("neutral"))["route"] == "OBSERVE_ONLY"


def test_knowledge_engine_main_push_becomes_recheck_candidate() -> None:
    payload = {
        "repository": {"full_name": "jweter/knowledge-engine-ai"},
        "ref": "refs/heads/main",
        "after": "b" * 40,
        "deleted": False,
    }
    assert route_knowledge_engine_push(payload)["route"] == "DEPENDENCY_RECHECK_CANDIDATE"


def test_non_main_push_is_ignored() -> None:
    payload = {
        "repository": {"full_name": "jweter/knowledge-engine-ai"},
        "ref": "refs/heads/agent/work",
        "after": "b" * 40,
    }
    assert route_knowledge_engine_push(payload)["route"] == "IGNORE"


def _load_workflow(name: str) -> dict:
    return json.loads((WORKFLOWS / name).read_text(encoding="utf-8"))


def test_committed_n8n_workflows_validate() -> None:
    assert set(validate_workflow_directory(WORKFLOWS)) == {
        "ci-failure-router.json",
        "knowledge-engine-impact-router.json",
    }


def test_validator_rejects_active_workflow() -> None:
    workflow = _load_workflow("ci-failure-router.json")
    workflow["active"] = True
    with pytest.raises(ValueError, match="inactive"):
        validate_workflow_document(workflow)


def test_validator_rejects_committed_credentials() -> None:
    workflow = _load_workflow("ci-failure-router.json")
    workflow["nodes"][0]["credentials"] = {"githubApi": {"id": "secret"}}
    with pytest.raises(ValueError, match="credential"):
        validate_workflow_document(workflow)


def test_validator_rejects_execute_command() -> None:
    workflow = _load_workflow("ci-failure-router.json")
    workflow["nodes"].append(
        {
            "parameters": {"command": "rm -rf /"},
            "type": "n8n-nodes-base.executeCommand",
            "typeVersion": 1,
            "position": [0, 0],
            "id": "bad",
            "name": "Bad",
        }
    )
    with pytest.raises(ValueError, match="disallowed node type"):
        validate_workflow_document(workflow)


def test_validator_rejects_github_mutation_other_than_dispatch() -> None:
    workflow = _load_workflow("ci-failure-router.json")
    dispatch = next(node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.github")
    dispatch["parameters"]["resource"] = "file"
    dispatch["parameters"]["operation"] = "create"
    with pytest.raises(ValueError, match="only dispatch"):
        validate_workflow_document(workflow)


def test_validator_rejects_network_capability_in_code_node() -> None:
    workflow = _load_workflow("ci-failure-router.json")
    classifier = next(node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.code")
    classifier["parameters"]["jsCode"] = "return await fetch('https://example.com');"
    with pytest.raises(ValueError, match="forbidden Code node capability"):
        validate_workflow_document(workflow)
