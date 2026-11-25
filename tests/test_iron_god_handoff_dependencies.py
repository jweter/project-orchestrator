import json

import pytest

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_handoff_dependencies as dependencies
from project_orchestrator import iron_god_observability as obs
from project_orchestrator import iron_god_structured_handoff as structured

REPO = obs.REPOSITORIES[0]


def item(ident, *, status="proposed", deps=None, repository=REPO):
    result = {key: "descriptive value" for key in backlog.REQUIRED_TEXT}
    result.update({key: [] for key in backlog.REQUIRED_LISTS})
    result.update(id=ident, repository=repository, status=status, level=3,
                  depends_on=deps or [])
    return result


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "release"
    state = tmp_path / "state"
    lane = state / "iron-god-products"
    lane.mkdir(parents=True)
    definition = repo / backlog.BACKLOG_PATH
    definition.parent.mkdir(parents=True)
    definition.write_text(json.dumps({"items": [
        item("target", deps=["prior"]), item("prior"),
    ]}), encoding="utf-8")
    ledger = backlog.state_path(lane)
    ledger.write_text(json.dumps({"schema_version": 1, "items": {
        "prior": {"outcome": "MERGED"},
    }}), encoding="utf-8")
    db = tmp_path / "mail.sqlite"
    request = structured.open_engineering_handoff(
        db, sender_label="chatgpt", recipient="iron-god", idempotency_key="deps",
        payload={"protocol": structured.PROTOCOL, "repository": REPO,
                 "milestone": "S2", "slice_id": "target", "commit_sha": "a" * 40,
                 "status": "PROPOSED", "tests": [], "pull_request": None,
                 "dependencies": ["prior"], "next_action": "REVIEW", "failure": "NONE"})
    return db, request["request_id"], repo, state, definition, ledger


def check(world, participant="iron-god"):
    db, ident, repo, state, _, _ = world
    return dependencies.reconcile_dependencies(db, participant=participant, request_id=ident,
                                               release_repo=repo, state_root=state)


def test_canonical_completion_does_not_grant_remote_acceptance(world):
    before = world[5].read_bytes()
    result = check(world)
    assert result["canonical_dependencies_satisfied"]
    assert result["dependencies"] == [{"slice_id": "prior", "status": "CANONICAL_COMPLETE"}]
    assert not result["dependencies_verified"] and not result["can_execute"]
    assert world[5].read_bytes() == before


@pytest.mark.parametrize(("row", "expected"), [
    ({"outcome": "ACCEPTED_LOCAL"}, "IN_FLIGHT"),
    ({"outcome": "MERGED", "terminal_outcome_conflict": True}, "CONFLICT"),
    ({"outcome": "FAIL"}, "OUTSTANDING"),
])
def test_unsettled_dependencies(world, row, expected):
    world[5].write_text(json.dumps({"schema_version": 1, "items": {"prior": row}}))
    result = check(world)
    assert not result["canonical_dependencies_satisfied"]
    assert result["dependencies"][0]["status"] == expected


@pytest.mark.parametrize("mode", ["omitted", "wrong_repo", "retired", "duplicate"])
def test_definition_conflicts(world, mode):
    data = json.loads(world[4].read_text())
    if mode == "omitted":
        data["items"][0]["depends_on"].append("other")
    elif mode == "wrong_repo":
        data["items"][1]["repository"] = obs.REPOSITORIES[1]
    elif mode == "retired":
        data["items"][1]["status"] = "retired"
    else:
        data["items"].append(data["items"][1])
    world[4].write_text(json.dumps(data))
    assert not check(world)["canonical_dependencies_satisfied"]


@pytest.mark.parametrize("raw", ['{"schema_version":2,"items":{}}',
                                 '{"schema_version":1,"items":{"prior":[]}}',
                                 '{"schema_version":1,"items":{},"items":{}}',
                                 "bad"])
def test_corrupt_state(world, raw):
    world[5].write_text(raw)
    assert check(world)["status"] == "UNVERIFIED"


def test_unrelated_participant_refused(world):
    with pytest.raises(ValueError, match="TRACKED_REQUEST_NOT_FOUND"):
        check(world, "claude-code")
