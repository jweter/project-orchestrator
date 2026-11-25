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

def test_scoped_read_validates_tracking_and_does_not_ack(tmp_path, payload):
    db = tmp_path / "mailbox.sqlite"
    request = handoff.open_engineering_handoff(
        db, sender_label="chatgpt", recipient="iron-god", payload=payload, idempotency_key="scoped",
    )
    for participant in ("chatgpt", "iron-god"):
        result = handoff.read_engineering_handoff(db, participant=participant, request_id=request["request_id"])
        assert result["payload"] == payload and result["can_execute"] is False
    with pytest.raises(ValueError, match="TRACKED_REQUEST_NOT_FOUND"):
        handoff.read_engineering_handoff(db, participant="claude-code", request_id=request["request_id"])
    raw = mail.post_message(
        db, sender_label="chatgpt", recipient="iron-god", kind="decision_request",
        body=handoff.encode_handoff(payload), reference=payload["repository"] + "@" + payload["commit_sha"],
        idempotency_key="raw",
    )
    with pytest.raises(ValueError, match="TRACKED_REQUEST_NOT_FOUND"):
        handoff.read_engineering_handoff(db, participant="iron-god", request_id=raw["message_id"])
    assert mail.list_pending_messages(db, recipient_label="iron-god")["pending_total"] == 2


def test_tracked_reference_mismatch_is_rejected(tmp_path, payload):
    db = tmp_path / "mailbox.sqlite"
    request = account.open_request(
        db, sender_label="chatgpt", recipient="iron-god", body=handoff.encode_handoff(payload),
        reference="PR#123", idempotency_key="mismatch",
    )
    with pytest.raises(ValueError, match="HANDOFF_REFERENCE_MISMATCH"):
        handoff.read_engineering_handoff(db, participant="iron-god", request_id=request["request_id"])

def test_freshness_routes_without_expensive_checks_for_expired_or_other_repo(tmp_path, payload, monkeypatch):
    from datetime import datetime, timezone

    db = tmp_path / "mailbox.sqlite"
    request = handoff.open_engineering_handoff(
        db, sender_label="chatgpt", recipient="iron-god", payload=payload,
        idempotency_key="expired", deadline_at_utc="2025-01-01T00:00:00Z",
    )
    def unexpected_git(_):
        pytest.fail("unnecessary Git read")

    monkeypatch.setattr(handoff.bridge, "_repository_head", unexpected_git)
    args = dict(participant="iron-god", request_id=request["request_id"],
                configured_repository=payload["repository"], repo_root=tmp_path)
    expired = handoff.assess_handoff_freshness(db, **args, now=datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert expired["status"] == "EXPIRED" and not expired["can_execute"]
    other = handoff.assess_handoff_freshness(
        db, **{**args, "configured_repository": obs.REPOSITORIES[1]},
        now=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert other["reason"] == "CONFIGURED_REPOSITORY_MISMATCH"


@pytest.mark.parametrize(("head", "expected"), [
    ({"status": "UNAVAILABLE"}, "UNVERIFIED"),
    ({"status": "OK", "head_sha": "bad"}, "UNVERIFIED"),
    ({"status": "OK", "head_sha": "b" * 40}, "HEAD_MISMATCH"),
    ({"status": "OK", "head_sha": "a" * 40}, "LOCAL_HEAD_MATCH"),
])
def test_freshness_is_advisory_and_does_not_ack(tmp_path, payload, monkeypatch, head, expected):
    db = tmp_path / "mailbox.sqlite"
    request = handoff.open_engineering_handoff(
        db, sender_label="chatgpt", recipient="iron-god", payload=payload, idempotency_key="freshness",
    )
    monkeypatch.setattr(handoff.bridge, "_repository_head", lambda root: head)
    result = handoff.assess_handoff_freshness(
        db, participant="iron-god", request_id=request["request_id"],
        configured_repository=payload["repository"], repo_root=tmp_path,
    )
    assert result["status"] == expected and result["work_type"] == "REVIEW"
    assert not any(result[key] for key in ("can_execute", "remote_head_verified",
                                           "test_evidence_verified", "dependencies_verified"))
    assert mail.list_pending_messages(db, recipient_label="iron-god")["pending_total"] == 1

def test_structured_handoff_mcp_scopes_reader_and_writer(tmp_path, payload, monkeypatch):
    import sys
    import types
    from project_orchestrator import iron_god_mcp_server as gateway

    class Server:
        def __init__(self, name):
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn
            return register

    module = types.ModuleType("mcp.server")
    module.MCPServer = Server
    monkeypatch.setitem(sys.modules, "mcp", types.ModuleType("mcp"))
    monkeypatch.setitem(sys.modules, "mcp.server", module)
    db = tmp_path / "mailbox.sqlite"
    args = dict(database=db, repo_root=tmp_path, state_root=tmp_path)
    sender = gateway.create_server(**args, client_label="chatgpt", allow_advisory_writes=True)
    reader = gateway.create_server(**args, client_label="iron-god")
    other = gateway.create_server(**args, client_label="claude-code")
    assert "post_engineering_handoff" not in reader.tools
    result = sender.tools["post_engineering_handoff"]("iron-god", payload, "mcp-structured")
    ident = result["request_id"]
    assert reader.tools["get_engineering_handoff"](ident)["payload"] == payload
    with pytest.raises(ValueError, match="TRACKED_REQUEST_NOT_FOUND"):
        other.tools["get_engineering_handoff"](ident)
    assert mail.list_pending_messages(db, recipient_label="iron-god")["pending_total"] == 1
