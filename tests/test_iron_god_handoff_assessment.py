from datetime import datetime, timedelta, timezone

import pytest

from project_orchestrator import iron_god_handoff_assessment as assessment
from project_orchestrator import iron_god_structured_handoff as structured
from project_orchestrator import iron_god_observability as obs
from project_orchestrator.github_evidence import GitHubError


def make(tmp_path, deadline=None):
    db = tmp_path / "mail.sqlite"
    payload = {"protocol": structured.PROTOCOL, "repository": obs.REPOSITORIES[0],
               "milestone": "S2", "slice_id": "slice", "commit_sha": "a" * 40,
               "status": "TESTED", "tests": ["actions:123"], "pull_request": 7,
               "dependencies": [], "next_action": "REVIEW", "failure": "NONE"}
    row = structured.open_engineering_handoff(
        db, sender_label="chatgpt", recipient="iron-god", payload=payload,
        idempotency_key="test", deadline_at_utc=deadline)
    return dict(database=db, participant="iron-god", request_id=row["request_id"],
                release_repo=tmp_path, state_root=tmp_path), payload


class Remote:
    def __init__(self, payload, changed=False):
        self.calls = []
        self.payload = payload
        self.changed = changed

    def get_json(self, path):
        self.calls.append(path)
        if self.changed:
            return {}
        repo = {"full_name": self.payload["repository"]}
        return {"number": 7, "state": "open",
                "head": {"sha": self.payload["commit_sha"], "repo": repo},
                "base": {"repo": repo}}


def test_local_default_never_queries_remote(tmp_path):
    args, payload = make(tmp_path)
    remote = Remote(payload)
    result = assessment.assess_handoff(**args, transport=remote)
    assert not remote.calls
    assert result["remote"]["status"] == "NOT_REQUESTED"
    assert not result["can_execute"]
    assert "LOCAL_DEPENDENCIES" in result["outstanding"]


def test_scope_precedes_remote(tmp_path):
    args, payload = make(tmp_path)
    remote = Remote(payload)
    args["participant"] = "claude-code"
    with pytest.raises(ValueError):
        assessment.assess_handoff(**args, include_remote=True, transport=remote)
    assert not remote.calls


def test_composed_success_still_not_authority(tmp_path, monkeypatch):
    args, payload = make(tmp_path)
    monkeypatch.setattr(assessment.dependencies, "reconcile_dependencies",
                        lambda *a, **k: {"canonical_dependencies_satisfied": True})
    monkeypatch.setattr(assessment.evidence, "verify_handoff",
                        lambda *a, **k: {"remote_head_verified": True, "test_evidence_verified": True})
    monkeypatch.setattr(assessment.coverage, "observe_required_coverage",
                        lambda *a, **k: {"required_coverage_verified": True})
    remote = Remote(payload)
    result = assessment.assess_handoff(**args, include_remote=True, transport=remote)
    assert result["outstanding"] == ["INDEPENDENT_DEPENDENCY_ACCEPTANCE", "REVIEW",
                                     "PRODUCT_ACCEPTANCE", "OWNER_AUTHORITY"]
    assert not result["can_execute"] and not result["dependencies_verified"]
    changed = assessment.assess_handoff(**args, include_remote=True, transport=Remote(payload, True))
    assert "REMOTE_HEAD" in changed["outstanding"] and "REQUIRED_CI" in changed["outstanding"]


def test_budget_exhaustion():
    remote = Remote({"repository": obs.REPOSITORIES[0], "commit_sha": "a" * 40})
    bounded = assessment.BoundedTransport(remote)
    for _ in range(assessment.MAX_REMOTE_REQUESTS):
        bounded.get_json("/test")
    with pytest.raises(GitHubError):
        bounded.get_json("/test")
    assert len(remote.calls) == assessment.MAX_REMOTE_REQUESTS


def test_expiration_after_readers(tmp_path, monkeypatch):
    args, payload = make(tmp_path, (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    real_read = assessment.structured.read_engineering_handoff

    def read(*a, **k):
        return {**real_read(*a, **k), "deadline_at_utc": "2000-01-01T00:00:00+00:00"}

    monkeypatch.setattr(assessment.structured, "read_engineering_handoff", read)
    remote = Remote(payload)
    result = assessment.assess_handoff(**args, include_remote=True, transport=remote)
    assert result["status"] == "EXPIRED" and not remote.calls
