from __future__ import annotations

from project_orchestrator.required_checks import (
    RequiredCheckResolver,
    resolve_required_checks,
)


class FakeTransport:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get_json(self, path: str) -> object:
        self.calls.append(path)
        value = self.responses.get(path)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise AssertionError(f"unexpected GitHub request: {path}")
        return value


def repository(
    *,
    checks: list[dict],
    manifest_names: list[str] | None = None,
) -> dict:
    verification = (
        {"mandatory_check_names": manifest_names}
        if manifest_names is not None
        else {"mandatory_check_names": None}
    )
    return {
        "name": "jweter/example",
        "default_branch": "main",
        "control_plane": {
            "present": True,
            "parse_state": "PARSED",
            "verification": verification,
        },
        "open_prs": [
            {
                "number": 1,
                "base_ref": "main",
                "head_sha": "head",
                "check_runs": checks,
                "required_checks": [],
                "required_checks_state": "UNKNOWN",
            }
        ],
    }


def policy_responses(*, rules: list[dict], legacy: object) -> dict[str, object]:
    return {
        "/repos/jweter/example/rules/branches/main": rules,
        "/repos/jweter/example/branches/main/protection/required_status_checks": legacy,
    }


def test_effective_rule_check_is_required_when_observed() -> None:
    repo = repository(
        checks=[
            {
                "id": 10,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
            }
        ],
        manifest_names=["CI"],
    )
    rules = [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "CI"}]},
        }
    ]
    resolved = RequiredCheckResolver(
        FakeTransport(policy_responses(rules=rules, legacy={"contexts": []}))
    ).resolve_repository(repo)
    pr = resolved["open_prs"][0]
    assert pr["required_checks_state"] == "RESOLVED"
    assert pr["required_checks"][0]["name"] == "CI"
    assert pr["required_checks"][0]["source"] == "live_branch_policy"


def test_missing_live_required_check_is_explicit() -> None:
    repo = repository(checks=[], manifest_names=[])
    rules = [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "Security"}]},
        }
    ]
    resolved = RequiredCheckResolver(
        FakeTransport(policy_responses(rules=rules, legacy={"contexts": []}))
    ).resolve_repository(repo)
    pr = resolved["open_prs"][0]
    assert pr["required_checks_state"] == "MISSING"
    assert pr["required_checks"] == [
        {
            "name": "Security",
            "id": None,
            "status": "missing",
            "conclusion": None,
            "source": "live_branch_policy",
        }
    ]


def test_manifest_only_missing_name_is_uncertain_not_permanently_required() -> None:
    repo = repository(
        checks=[
            {
                "id": 10,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
            }
        ],
        manifest_names=["CI", "Retired Check"],
    )
    resolved = RequiredCheckResolver(
        FakeTransport(policy_responses(rules=[], legacy={"contexts": []}))
    ).resolve_repository(repo)
    pr = resolved["open_prs"][0]
    assert pr["required_checks_state"] == "UNCERTAIN"
    assert pr["required_check_policy"]["unconfirmed_manifest_check_names"] == [
        "Retired Check"
    ]
    assert [check["name"] for check in pr["required_checks"]] == ["CI"]


def test_confirmed_none_requires_live_rules_and_explicit_empty_manifest() -> None:
    repo = repository(checks=[], manifest_names=[])
    resolved = RequiredCheckResolver(
        FakeTransport(policy_responses(rules=[], legacy={"contexts": []}))
    ).resolve_repository(repo)
    assert resolved["open_prs"][0]["required_checks_state"] == "CONFIRMED_NONE"


def test_required_workflow_rule_fails_closed_until_mapped() -> None:
    repo = repository(checks=[], manifest_names=[])
    rules = [
        {
            "type": "required_workflows",
            "parameters": {
                "workflows": [
                    {
                        "path": ".github/workflows/security.yml",
                        "repository_id": 1,
                        "ref": "refs/heads/main",
                    }
                ]
            },
        }
    ]
    resolved = RequiredCheckResolver(
        FakeTransport(policy_responses(rules=rules, legacy={"contexts": []}))
    ).resolve_repository(repo)
    assert resolved["open_prs"][0]["required_checks_state"] == "UNCERTAIN"


def test_latest_check_run_wins_for_duplicate_context() -> None:
    repo = repository(
        checks=[
            {
                "id": 10,
                "name": "CI",
                "status": "completed",
                "conclusion": "failure",
            },
            {
                "id": 11,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
            },
        ],
        manifest_names=["CI"],
    )
    rules = [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "CI"}]},
        }
    ]
    resolved = RequiredCheckResolver(
        FakeTransport(policy_responses(rules=rules, legacy={"contexts": []}))
    ).resolve_repository(repo)
    assert resolved["open_prs"][0]["required_checks"][0]["id"] == 11


def test_portfolio_resolution_preserves_project_boundary() -> None:
    snapshot = {
        "required_check_interpretation": "UNKNOWN",
        "projects": [
            {
                "id": "everward",
                "repositories": [repository(checks=[], manifest_names=[])],
            }
        ],
    }
    resolved = resolve_required_checks(
        snapshot,
        FakeTransport(policy_responses(rules=[], legacy={"contexts": []})),
    )
    assert resolved["projects"][0]["id"] == "everward"
    assert resolved["required_check_interpretation"] == "LIVE_POLICY_RESOLVED"
