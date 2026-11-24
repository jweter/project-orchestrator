"""Structured handoff contract: shape validation is not engineering authority."""
import json
import sqlite3

import pytest

from project_orchestrator import iron_god_observability as obs
from project_orchestrator import iron_god_structured_handoff as handoff
from project_orchestrator import mcp_coordination as mail
from project_orchestrator import mcp_coordination_accountability as account


@pytest.fixture
def payload():
    return {
        "protocol": handoff.PROTOCOL, "repository": obs.REPOSITORIES[0],
        "milestone": "S2.3", "slice_id": "slice-1", "commit_sha": "a" * 40,
        "status": "TESTED", "tests": ["pytest:run-1"], "pull_request": 790,
        "dependencies": ["slice-0"], "next_action": "REVIEW", "failure": "NONE",
    }


@pytest.mark.parametrize("repository", obs.REPOSITORIES)
def test_all_registered_repository_envelopes(payload, repository):
    payload["repository"] = repository
    assert handoff.decode_handoff(handoff.encode_handoff(payload)) == payload


@pytest.mark.parametrize(("field", "value"), [
    ("repository", "unknown/repo"), ("commit_sha", "abc"),
    ("commit_sha", "0" * 40), ("pull_request", True),
    ("tests", []), ("tests", ["same", "same"]), ("tests", ["sk-secret123456789"]),
    ("dependencies", ["slice-1"]), ("next_action", "EXECUTE_SHELL"),
    ("failure", "TEST"), ("status", "BLOCKED"), ("milestone", ""),
    ("protocol", "future"), ("tests", [{}]), ("status", {}),
])
def test_invalid_payload_never_creates_database(tmp_path, payload, field, value):
    payload[field] = value
    db = tmp_path / "mailbox.sqlite"
    with pytest.raises(ValueError):
        handoff.open_engineering_handoff(db, sender_label="chatgpt", recipient="iron-god",
                                         payload=payload, idempotency_key="invalid")
    assert not db.exists()


def test_duplicate_json_fields_and_unknown_authority_rejected(payload):
    body = handoff.encode_handoff(payload)
    with pytest.raises(ValueError):
        handoff.decode_handoff(body[:-1] + ',"status":"TESTED"}')
    with pytest.raises(ValueError):
        handoff.encode_handoff({**payload, "authorized": True})


def test_existing_tracked_request_lifecycle_and_idempotency(tmp_path, payload):
    db = tmp_path / "mailbox.sqlite"
    args = dict(sender_label="chatgpt", recipient="iron-god", payload=payload, idempotency_key="handoff")
    first = handoff.open_engineering_handoff(db, **args)
    second = handoff.open_engineering_handoff(db, **args)
    assert first["request_id"] == second["request_id"] and second["replayed"]
    row = mail.list_messages(db, recipient_label="iron-god")["messages"][0]
    assert json.loads(row["body"]) == payload
    assert row["reference"] == payload["repository"] + "@" + payload["commit_sha"]
    assert not row["acknowledged"]
    with pytest.raises(ValueError):
        handoff.open_engineering_handoff(db, **{**args, "payload": {**payload, "commit_sha": "b" * 40}})
    account.record_disposition(db, actor_label="iron-god", request_id=first["request_id"],
                               state="ACCEPTED", event_key="accepted")
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tracked_requests").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
