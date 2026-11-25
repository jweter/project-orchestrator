import base64
import json

import pytest

from project_orchestrator.github_evidence import GitHubError
from project_orchestrator.iron_god_required_coverage import observe_required_coverage
from project_orchestrator.iron_god_observability import REPOSITORIES

REPO = REPOSITORIES[0]
SHA = "a" * 40


class Remote:
    def __init__(self):
        self.pr = {"number": 7, "state": "open",
                   "head": {"sha": SHA, "repo": {"full_name": REPO}},
                   "base": {"ref": "main", "repo": {"full_name": REPO}}}
        self.checks = [{"id": 1, "name": "CI", "head_sha": SHA,
                        "app": {"id": 7}, "status": "completed", "conclusion": "success"}]
        self.rules = [{"type": "required_status_checks", "parameters": {
            "required_status_checks": [{"context": "CI", "integration_id": 7}]}}]
        self.manifest = {"verification": {"mandatory_check_names": ["CI"]}}
        self.calls = []
        self.changed = False
        self.offline = False

    def get_json(self, path):
        self.calls.append(path)
        if "/pulls/" in path:
            return {} if self.changed and self.calls.count(path) > 1 else self.pr
        if "/check-runs" in path:
            return {"check_runs": self.checks}
        if "/statuses" in path:
            return []
        if "/contents/" in path:
            return {"content": base64.b64encode(json.dumps(self.manifest).encode()).decode()}
        if "/rules/" in path:
            if self.offline:
                raise GitHubError("private detail")
            return self.rules
        if "/protection/" in path:
            return {"contexts": [], "checks": []}
        raise AssertionError(path)


def test_required_coverage_uses_live_policy_and_exact_head():
    result = observe_required_coverage(REPO, SHA, 7, Remote())
    assert result["required_coverage_verified"] and not result["can_execute"]
    assert result["required_checks_state"] == "RESOLVED"


@pytest.mark.parametrize(("field", "value"), [
    ("head_sha", "b" * 40), ("conclusion", "failure"), ("status", "in_progress"),
    ("app", {"id": 8}),
])
def test_wrong_check_never_satisfies_coverage(field, value):
    remote = Remote()
    remote.checks[0][field] = value
    assert not observe_required_coverage(REPO, SHA, 7, remote)["required_coverage_verified"]


def test_missing_manifest_requirement_is_not_covered():
    remote = Remote()
    remote.manifest["verification"]["mandatory_check_names"].append("Preflight")
    assert not observe_required_coverage(REPO, SHA, 7, remote)["required_coverage_verified"]


def test_head_change_and_policy_unavailability_fail_closed():
    remote = Remote()
    remote.changed = True
    assert observe_required_coverage(REPO, SHA, 7, remote)["reason"] == "PULL_REQUEST_SCOPE_CHANGED"
    remote = Remote()
    remote.offline = True
    result = observe_required_coverage(REPO, SHA, 7, remote)
    assert not result["required_coverage_verified"] and "private detail" not in str(result)


def test_invalid_scope_never_queries_network():
    remote = Remote()
    with pytest.raises(ValueError):
        observe_required_coverage("unknown/repo", SHA, 7, remote)
    assert not remote.calls
