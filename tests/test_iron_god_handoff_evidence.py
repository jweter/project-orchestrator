"""Independent remote evidence does not authorize advisory work."""
from datetime import datetime, timezone

import pytest

from project_orchestrator import iron_god_handoff_evidence as evidence
from project_orchestrator import iron_god_structured_handoff as structured
from project_orchestrator import iron_god_observability as obs
from project_orchestrator.github_evidence import GitHubError

SHA = "a" * 40
REPO = obs.REPOSITORIES[0]


class Remote:
    def __init__(self):
        self.calls = []
        self.pr = {"number": 7, "state": "open",
                   "head": {"sha": SHA, "repo": {"full_name": REPO}},
                   "base": {"repo": {"full_name": REPO}}}
        self.run = {"id": 123, "head_sha": SHA, "status": "completed",
                    "conclusion": "success", "repository": {"full_name": REPO},
                    "head_repository": {"full_name": REPO}}
        self.changed = False

    def get_json(self, path):
        self.calls.append(path)
        if "/commits/" in path:
            return {"sha": SHA}
        if "/pulls/" in path:
            if self.changed and self.calls.count(path) > 1:
                return {}
            return self.pr
        if "/actions/runs/" in path:
            return self.run
        raise AssertionError(path)


@pytest.fixture
def world(tmp_path):
    db = tmp_path / "mail.sqlite"
    payload = {"protocol": structured.PROTOCOL, "repository": REPO,
               "milestone": "S2", "slice_id": "one", "commit_sha": SHA,
               "status": "TESTED", "tests": ["actions:123"], "pull_request": 7,
               "dependencies": ["prior"], "next_action": "REVIEW", "failure": "NONE"}
    request = structured.open_engineering_handoff(
        db, sender_label="chatgpt", recipient="iron-god", payload=payload,
        idempotency_key="test")
    return db, request["request_id"]


def check(world, remote, participant="iron-god"):
    return evidence.verify_handoff(world[0], participant=participant,
                                   request_id=world[1], transport=remote)


def test_exact_head_and_run_are_observed_but_not_authority(world):
    remote = Remote()
    result = check(world, remote)
    assert result["commit_verified"] and result["remote_head_verified"]
    assert result["test_evidence_verified"] and result["verified_tests"] == ["actions:123"]
    assert not result["dependencies_verified"] and not result["can_execute"]
    assert len(remote.calls) == 4


@pytest.mark.parametrize(("field", "value"), [
    ("head_sha", "b" * 40), ("conclusion", "failure"), ("status", "in_progress"),
    ("id", True), ("repository", {"full_name": "other/repo"}),
    ("head_repository", {"full_name": "other/repo"}),
])
def test_wrong_run_never_verifies(world, field, value):
    remote = Remote()
    remote.run[field] = value
    result = check(world, remote)
    assert result["remote_head_verified"] and not result["test_evidence_verified"]
    assert not result["can_execute"]


def test_changed_head_and_wrong_repo_refused(world):
    remote = Remote()
    remote.changed = True
    assert check(world, remote)["reason"] == "PULL_REQUEST_HEAD_CHANGED"
    remote = Remote()
    remote.pr["head"]["repo"]["full_name"] = "other/repo"
    assert check(world, remote)["reason"] == "PULL_REQUEST_HEAD_MISMATCH"


def test_unrelated_participant_cannot_trigger_network(world):
    remote = Remote()
    with pytest.raises(ValueError, match="TRACKED_REQUEST_NOT_FOUND"):
        check(world, remote, "claude-code")
    assert not remote.calls


def test_transport_failure_is_sanitized(world):
    class Offline:
        def get_json(self, path):
            raise GitHubError("sensitive diagnostic")

    result = check(world, Offline())
    assert result["status"] == "UNVERIFIED" and not result["can_execute"]
    assert "sensitive" not in str(result)


def test_expired_request_never_calls_remote(tmp_path):
    db = tmp_path / "mail.sqlite"
    request = structured.open_engineering_handoff(
        db, sender_label="chatgpt", recipient="iron-god", idempotency_key="expired",
        deadline_at_utc="2099-01-01T00:00:00+00:00",
        payload={"protocol": structured.PROTOCOL, "repository": REPO,
                 "milestone": "S2", "slice_id": "one", "commit_sha": SHA,
                 "status": "PROPOSED", "tests": [], "pull_request": None,
                 "dependencies": [], "next_action": "REVIEW", "failure": "NONE"})
    remote = Remote()
    result = evidence.verify_handoff(db, participant="iron-god",
                                     request_id=request["request_id"], transport=remote,
                                     now=datetime(2099, 1, 2, tzinfo=timezone.utc))
    assert result["status"] == "EXPIRED" and not remote.calls
