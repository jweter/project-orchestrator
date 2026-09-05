from project_orchestrator.github_evidence import GitHubError
from project_orchestrator.required_checks import RequiredCheckResolver


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses

    def get_json(self, path):
        value = self.responses[path]
        if isinstance(value, Exception):
            raise value
        return value


def repository(checks=None, statuses=None, manifest_names=None):
    return {
        "name": "jweter/example",
        "default_branch": "main",
        "control_plane": {
            "present": True,
            "parse_state": "PARSED",
            "verification": {"mandatory_check_names": manifest_names if manifest_names is not None else []},
        },
        "open_prs": [{
            "number": 1, "base_ref": "main", "head_sha": "head",
            "check_runs": checks or [], "status_contexts": statuses or [],
            "required_checks": [], "required_checks_state": "UNKNOWN",
        }],
    }


def policy(rules=None, legacy=None):
    return {
        "/repos/jweter/example/rules/branches/main": rules or [],
        "/repos/jweter/example/branches/main/protection/required_status_checks": legacy or {"contexts": [], "checks": []},
    }


def test_effective_rule_check_is_required_when_observed() -> None:
    repo = repository(checks=[{"id": 10, "name": "CI", "status": "completed", "conclusion": "success", "app_id": 7}], manifest_names=["CI"])
    rules = [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "CI", "integration_id": 7}]}}]
    pr = RequiredCheckResolver(FakeTransport(policy(rules=rules))).resolve_repository(repo)["open_prs"][0]
    assert pr["required_checks_state"] == "RESOLVED"
    assert pr["required_checks"][0]["required_app_id"] == 7


def test_wrong_app_cannot_satisfy_pinned_required_check() -> None:
    repo = repository(checks=[{"id": 10, "name": "CI", "status": "completed", "conclusion": "success", "app_id": 8}], manifest_names=[])
    rules = [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "CI", "integration_id": 7}]}}]
    pr = RequiredCheckResolver(FakeTransport(policy(rules=rules))).resolve_repository(repo)["open_prs"][0]
    assert pr["required_checks_state"] == "MISSING"
    assert pr["required_checks"][0]["required_app_id"] == 7


def test_commit_status_satisfies_unpinned_required_context() -> None:
    repo = repository(statuses=[{"id": 20, "name": "legacy-ci", "status": "completed", "conclusion": "success", "app_id": None}], manifest_names=[])
    legacy = {"contexts": ["legacy-ci"], "checks": []}
    pr = RequiredCheckResolver(FakeTransport(policy(legacy=legacy))).resolve_repository(repo)["open_prs"][0]
    assert pr["required_checks_state"] == "RESOLVED"
    assert pr["required_checks"][0]["evidence_kind"] == "commit_status"


def test_commit_status_does_not_satisfy_app_pinned_context() -> None:
    repo = repository(statuses=[{"id": 20, "name": "CI", "status": "completed", "conclusion": "success", "app_id": None}], manifest_names=[])
    legacy = {"contexts": [], "checks": [{"context": "CI", "app_id": 7}]}
    pr = RequiredCheckResolver(FakeTransport(policy(legacy=legacy))).resolve_repository(repo)["open_prs"][0]
    assert pr["required_checks_state"] == "MISSING"


def test_required_workflow_rule_fails_closed_until_mapped() -> None:
    repo = repository(manifest_names=[])
    rules = [{"type": "required_workflows", "parameters": {"workflows": [{"path": ".github/workflows/security.yml", "repository_id": 1, "ref": "refs/heads/main"}]}}]
    pr = RequiredCheckResolver(FakeTransport(policy(rules=rules))).resolve_repository(repo)["open_prs"][0]
    assert pr["required_checks_state"] == "UNCERTAIN"


def test_confirmed_none_requires_live_rules_and_explicit_empty_manifest() -> None:
    pr = RequiredCheckResolver(FakeTransport(policy())).resolve_repository(repository(manifest_names=[]))["open_prs"][0]
    assert pr["required_checks_state"] == "CONFIRMED_NONE"


def test_inaccessible_live_policy_fails_closed_instead_of_crashing() -> None:
    repo = repository(
        checks=[{"id": 10, "name": "CI", "status": "completed", "conclusion": "success", "app_id": 7}],
        manifest_names=[],
    )
    responses = policy()
    responses["/repos/jweter/example/branches/main/protection/required_status_checks"] = GitHubError("403 forbidden")
    pr = RequiredCheckResolver(FakeTransport(responses)).resolve_repository(repo)["open_prs"][0]
    assert pr["required_checks_state"] == "UNCERTAIN"
    assert pr["required_check_policy"]["legacy_branch_protection_state"] == "UNAVAILABLE"
    assert pr["required_check_policy"]["policy_access_errors"]
