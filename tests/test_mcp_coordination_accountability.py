"""End-to-end tests for the incremental #675 advisory request/disposition protocol."""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from project_orchestrator import iron_god_mcp_cli as cli
from project_orchestrator import mcp_coordination as mail
from project_orchestrator import mcp_coordination_accountability as account


@pytest.fixture
def ctx(tmp_path):
    state = tmp_path / "state"
    repo = tmp_path / "repo"
    state.mkdir()
    repo.mkdir()
    return {"db": tmp_path / "mailbox.sqlite", "state": state, "repo": repo}


def run(ctx, who, *argv, writes=False):
    env = {
        "IRON_GOD_MCP_COORDINATION_DB": str(ctx["db"]),
        "IRON_GOD_MCP_STATE_ROOT": str(ctx["state"]),
        "IRON_GOD_MCP_REPO_ROOT": str(ctx["repo"]),
        "IRON_GOD_MCP_CLIENT_LABEL": who,
    }
    if writes:
        env["IRON_GOD_MCP_ALLOW_ADVISORY_WRITES"] = "1"
    out = io.StringIO()
    rc = cli.run(list(argv), env=env, out=out)
    return rc, json.loads(out.getvalue())


def test_unconfigured_dashboard_never_creates_or_upgrades_live_database(ctx):
    assert run(ctx, "chatgpt", "requests")[1]["status"] == "UNINITIALIZED"
    assert not ctx["db"].exists()
    mail.initialize(ctx["db"])
    assert run(ctx, "chatgpt", "requests")[1]["status"] == "NOT_CONFIGURED"
    with sqlite3.connect(ctx["db"]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='tracked_requests'").fetchone() is None


def test_two_way_handoff_records_correlated_response_and_handled_receipt(ctx):
    rc, queued = run(ctx, "chatgpt", "handoff", "--to", "claude-code",
                     "--body", "Review PR#680", "--reference", "PR#680",
                     "--key", "review-680", writes=True)
    assert rc == 0 and queued["status"] == "QUEUED"
    ident = queued["request_id"]
    assert run(ctx, "chatgpt", "status")[1]["handoffs"]["requests"] == []
    incoming = run(ctx, "claude-code", "requests")[1]["requests"]
    assert len(incoming) == 1 and incoming[0]["state"] == "QUEUED"
    # The recipient's raw message is advisory; reading it never auto-ACKs it.
    assert run(ctx, "claude-code", "read")[1]["messages"][0]["acknowledged"] is False
    rc, acknowledged = run(ctx, "claude-code", "disposition", "--id", ident,
                           "--state", "ACCEPTED", writes=True)
    assert rc == 0 and acknowledged["status"] == "ACCEPTED"
    response = run(ctx, "chatgpt", "read")[1]["messages"][0]
    payload = json.loads(response["body"])
    assert payload["protocol"] == "handoff_disposition_v1"
    assert payload["response_to"] == ident
    assert payload["disposition"] == "ACCEPTED"
    assert payload["authority"] == "ADVISORY_ONLY"
    assert payload["verification"] == "RECIPIENT_SELF_REPORTED"
    status = run(ctx, "claude-code", "requests")[1]["requests"][0]
    assert status["recipient_acknowledged"] is True and status["state"] == "ACCEPTED"
    assert run(ctx, "chatgpt", "requests", "--role", "outgoing")[1]["requests"][0]["state"] == "ACCEPTED"
    assert "Review PR#680" not in json.dumps(run(ctx, "claude-code", "status")[1]["handoffs"])


def test_idempotent_replays_do_not_duplicate_work_or_messages(ctx):
    first = account.open_request(ctx["db"], sender_label="chatgpt", recipient="claude-code",
                                 body="Review code", reference="PR#675", idempotency_key="review")
    again = account.open_request(ctx["db"], sender_label="chatgpt", recipient="claude-code",
                                 body="Review code", reference="PR#675", idempotency_key="review")
    assert first["request_id"] == again["request_id"] and again["replayed"]
    a = account.record_disposition(ctx["db"], actor_label="claude-code",
                                   request_id=first["request_id"], state="ACCEPTED", event_key="once")
    b = account.record_disposition(ctx["db"], actor_label="claude-code",
                                   request_id=first["request_id"], state="ACCEPTED", event_key="once")
    assert not a["replayed"] and b["replayed"]
    assert a["response_message_id"] == b["response_message_id"]
    with sqlite3.connect(ctx["db"]) as db:
        assert db.execute("SELECT count(*) FROM tracked_requests").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM request_dispositions").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 1
    assert len(mail.list_messages(ctx["db"], recipient_label="chatgpt")["messages"]) == 1


def test_recipient_can_work_then_complete_with_evidence_but_not_auto_approve(ctx):
    r = account.open_request(ctx["db"], sender_label="iron-god", recipient="chatgpt",
                             body="Review CI", reference="PR#123", idempotency_key="ci")
    req = r["request_id"]
    for state, reference in [("ACCEPTED", ""), ("WORKING", ""),
                             ("COMPLETED", "PR#123")]:
        account.record_disposition(ctx["db"], actor_label="chatgpt",
                                   request_id=req, state=state, evidence_reference=reference)
    view = account.requests_snapshot(ctx["db"], participant="iron-god", role="outgoing")
    assert view["requests"][0]["state"] == "COMPLETED"
    assert view["requests"][0]["verification"] == "RECIPIENT_SELF_REPORTED"
    with pytest.raises(ValueError, match="INVALID_DISPOSITION_TRANSITION"):
        account.record_disposition(ctx["db"], actor_label="chatgpt", request_id=req,
                                   state="WORKING", event_key="new-work-after-terminal")
    assert len(mail.list_messages(ctx["db"], recipient_label="iron-god")["messages"]) == 3


def test_deadline_surfaces_waiting_for_agent_without_clobbering_pending_message(ctx):
    start = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)
    r = account.open_request(ctx["db"], sender_label="chatgpt", recipient="claude-code",
                             body="Please review", reference="PR#680", idempotency_key="deadline",
                             deadline_at_utc=start.isoformat())
    before = account.requests_snapshot(ctx["db"], participant="chatgpt", role="outgoing",
                                       now=start - timedelta(seconds=30))["requests"][0]
    after = account.requests_snapshot(ctx["db"], participant="chatgpt", role="outgoing",
                                      now=end)["requests"][0]
    assert before["state"] == "QUEUED"
    assert after["state"] == "WAITING_FOR_AGENT" and after["overdue"]
    assert len(mail.list_pending_messages(ctx["db"], recipient_label="claude-code")["messages"]) == 1
    assert len(mail.list_messages(ctx["db"], recipient_label="chatgpt")["messages"]) == 0


def test_legacy_mailbox_acks_are_not_accepted_as_dispositions(ctx):
    r = account.open_request(ctx["db"], sender_label="chatgpt", recipient="claude-code",
                             body="Status", reference="issue:675", idempotency_key="status")
    mail.ack_message(ctx["db"], recipient_label="claude-code", message_id=r["request_id"])
    pending = account.requests_snapshot(ctx["db"], participant="chatgpt", role="outgoing")["requests"][0]
    assert pending["state"] == "DELIVERED_AWAITING_RESPONSE"
    assert pending["recipient_acknowledged"] is True
    assert pending["verification"] == "NONE"


@pytest.mark.parametrize("who,state,evidence,code", [
    ("chatgpt", "ACCEPTED", "", "ONLY_REQUEST_RECIPIENT_MAY_DISPOSE"),
    ("claude-code", "DISPATCH", "", "INVALID_DISPOSITION"),
    ("claude-code", "COMPLETED", "", "COMPLETION_REQUIRES_EVIDENCE_REFERENCE"),
    ("claude-code", "COMPLETED", "PR#123; rm -rf /", "ACTIONABLE_REFERENCE_REQUIRED"),
])
def test_bad_dispositions_fail_closed(ctx, who, state, evidence, code):
    req = account.open_request(ctx["db"], sender_label="chatgpt", recipient="claude-code",
                               body="Please review", reference="PR#123", idempotency_key="one")
    with pytest.raises(ValueError, match=code):
        account.record_disposition(ctx["db"], actor_label=who, request_id=req["request_id"],
                                   state=state, evidence_reference=evidence)
    assert len(mail.list_messages(ctx["db"], recipient_label="chatgpt")["messages"]) == 0
    assert len(account.requests_snapshot(ctx["db"], participant="chatgpt", role="outgoing")["requests"]) == 1


def test_terminal_blocker_requires_a_new_request_not_a_silent_reopen(ctx):
    req = account.open_request(ctx["db"], sender_label="chatgpt", recipient="claude-code",
                               body="P1 diagnosis", reference="issue:675", idempotency_key="block")
    account.record_disposition(ctx["db"], actor_label="claude-code", request_id=req["request_id"],
                               state="BLOCKED", evidence_reference="issue:675")
    with pytest.raises(ValueError, match="INVALID_DISPOSITION_TRANSITION"):
        account.record_disposition(ctx["db"], actor_label="claude-code",
                                   request_id=req["request_id"], state="COMPLETED",
                                   evidence_reference="PR#999")
    assert account.requests_snapshot(ctx["db"], participant="chatgpt", role="outgoing")["requests"][0]["state"] == "BLOCKED"


def test_immutable_events_and_unmodified_schema_version(ctx):
    r = account.open_request(ctx["db"], sender_label="chatgpt", recipient="claude-code",
                             body="Review", reference="PR#123", idempotency_key="one")
    account.record_disposition(ctx["db"], actor_label="claude-code", request_id=r["request_id"], state="ACCEPTED")
    with sqlite3.connect(ctx["db"]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_REQUEST"):
            db.execute("DELETE FROM tracked_requests")
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_DISPOSITION"):
            db.execute("UPDATE request_dispositions SET state='COMPLETED'")


def test_request_replay_changed_deadline_is_refused_without_changing_first_one(ctx):
    old = account.open_request(ctx["db"], sender_label="chatgpt", recipient="claude-code",
                               body="Review", reference="PR#123", idempotency_key="one",
                               deadline_at_utc="2026-11-09T14:00:00Z")
    with pytest.raises(ValueError, match="TRACKED_REQUEST_REPLAY_CONFLICT"):
        account.open_request(ctx["db"], sender_label="chatgpt", recipient="claude-code",
                             body="Review", reference="PR#123", idempotency_key="one",
                             deadline_at_utc="2026-11-10T14:00:00Z")
    record = account.requests_snapshot(ctx["db"], participant="chatgpt", role="outgoing")["requests"][0]
    assert record["request_id"] == old["request_id"]
    assert record["deadline_at_utc"] == "2026-11-09T14:00:00+00:00"


def test_deferred_event_recovery_posts_same_reply_once(ctx, monkeypatch):
    r = account.open_request(ctx["db"], sender_label="chatgpt", recipient="claude-code",
                             body="Please review", reference="PR#680", idempotency_key="one")
    original = mail.post_message
    def fail(*args, **kwargs):
        if kwargs.get("sender_label") == "claude-code":
            raise OSError("simulated crash after event commit")
        return original(*args, **kwargs)
    monkeypatch.setattr(mail, "post_message", fail)
    with pytest.raises(OSError, match="simulated crash"):
        account.record_disposition(ctx["db"], actor_label="claude-code",
                                   request_id=r["request_id"], state="ACCEPTED", event_key="v1")
    status = account.requests_snapshot(ctx["db"], participant="chatgpt", role="outgoing")["requests"][0]
    assert status["state"] == "ACCEPTED"
    assert status["reply_pending"] and not status["reply_persisted"]
    monkeypatch.setattr(mail, "post_message", original)
    with pytest.raises(ValueError, match="PREVIOUS_DISPOSITION_REPLY_PENDING"):
        account.record_disposition(ctx["db"], actor_label="claude-code",
                                   request_id=r["request_id"], state="WORKING")
    recovered = account.recover_pending_replies(ctx["db"], actor_label="claude-code")
    assert len(recovered["recovered"]) == 1
    assert account.recover_pending_replies(ctx["db"], actor_label="claude-code")["recovered"] == []
    ret = account.record_disposition(ctx["db"], actor_label="claude-code",
                                     request_id=r["request_id"], state="ACCEPTED", event_key="v1")
    assert ret["replayed"] is True
    final = account.requests_snapshot(ctx["db"], participant="chatgpt", role="outgoing")["requests"][0]
    assert final["reply_persisted"] and not final["reply_pending"]
    assert final["response_message_id"] == ret["response_message_id"]
    assert len(mail.list_messages(ctx["db"], recipient_label="chatgpt")["messages"]) == 1


def test_cli_writes_are_opt_in_and_filtered_to_the_connected_principal(ctx):
    assert run(ctx, "chatgpt", "handoff", "--to", "claude-code", "--body", "Hello",
               "--reference", "issue:675", "--key", "k")[0] == 1
    assert run(ctx, "claude-code", "disposition", "--id", "a" * 32, "--state", "ACCEPTED")[0] == 1
    assert not ctx["db"].exists()
    rc, r = run(ctx, "chatgpt", "handoff", "--to", "claude-code", "--body", "Hello",
                "--reference", "issue:675", "--key", "k", writes=True)
    assert rc == 0
    assert run(ctx, "chatgpt", "requests")[1]["requests"] == []
    assert len(run(ctx, "chatgpt", "requests", "--role", "outgoing")[1]["requests"]) == 1
    assert len(run(ctx, "iron-god", "requests", "--role", "all")[1]["requests"]) == 0
    assert run(ctx, "claude-code", "disposition", "--id", r["request_id"],
               "--state", "NEEDS_OWNER", writes=True)[0] == 0
    assert run(ctx, "chatgpt", "requests", "--role", "outgoing")[1]["requests"][0]["state"] == "NEEDS_OWNER"


def test_missing_tracked_request_cannot_turn_a_plain_advisory_into_a_work_order(ctx):
    old = mail.post_message(ctx["db"], sender_label="chatgpt", recipient="claude-code",
                            kind="handoff", body="General note", reference="PR#1",
                            idempotency_key="legacy")
    with pytest.raises(ValueError, match="TRACKED_REQUEST_NOT_FOUND"):
        account.record_disposition(ctx["db"], actor_label="claude-code",
                                   request_id=old["message_id"], state="COMPLETED",
                                   evidence_reference="PR#1")


def test_recovery_cli_respects_write_gate_and_never_invents_a_response(ctx, monkeypatch):
    req = account.open_request(ctx["db"], sender_label="chatgpt", recipient="claude-code",
                               body="Review", reference="PR#680", idempotency_key="recover")
    original = mail.post_message

    def fail_reply(*args, **kwargs):
        if kwargs.get("sender_label") == "claude-code":
            raise OSError("offline")
        return original(*args, **kwargs)

    monkeypatch.setattr(mail, "post_message", fail_reply)
    with pytest.raises(OSError, match="offline"):
        account.record_disposition(ctx["db"], actor_label="claude-code",
                                   request_id=req["request_id"], state="BLOCKED", event_key="k1")
    monkeypatch.setattr(mail, "post_message", original)
    assert run(ctx, "claude-code", "repair-replies")[0] == 1
    assert run(ctx, "iron-god", "repair-replies", writes=True)[1]["recovered"] == []
    code, recovered = run(ctx, "claude-code", "repair-replies", "--limit", "1", writes=True)
    assert code == 0 and len(recovered["recovered"]) == 1
    assert run(ctx, "claude-code", "repair-replies", writes=True)[1]["recovered"] == []
    row = account.requests_snapshot(ctx["db"], participant="chatgpt", role="outgoing")["requests"][0]
    assert row["state"] == "BLOCKED" and row["reply_persisted"] and row["recipient_acknowledged"]
