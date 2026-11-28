"""Advisory MCP communications: no new execution or owner-control authority."""
from __future__ import annotations

import json
import sqlite3
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from project_orchestrator import mcp_coordination as co
from project_orchestrator import iron_god_mcp_server as gateway


@pytest.fixture
def database(tmp_path):
    return tmp_path / "coordination.sqlite"


def post(database, *, sender_label="claude-code", recipient="iron-god",
         kind="handoff", body="Repo jweter/test: verify SHA a1b2", idempotency_key="one",
         reference="jweter/test#main"):
    return co.post_message(database, sender_label=sender_label, recipient=recipient,
                           kind=kind, body=body, idempotency_key=idempotency_key,
                           reference=reference)


def test_versioned_schema_rejects_unknown_future_version(database):
    co.initialize(database)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        db.execute("PRAGMA user_version=400")
    with pytest.raises(ValueError, match="UNSUPPORTED_COORDINATION_SCHEMA"):
        post(database)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 400
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_durable_replay_returns_identical_message_and_one_row(database):
    initial = post(database)
    second = post(database)
    assert initial["message_id"] == second["message_id"]
    assert initial["replayed"] is False and second["replayed"] is True
    assert second["seq"] == 1
    view = co.list_messages(database, recipient_label="iron-god")
    assert view["status"] == "OK"
    assert [x["message_id"] for x in view["messages"]] == [initial["message_id"]]
    assert view["messages"][0]["acknowledged"] is False


def test_changed_payload_under_same_key_fails_without_overwriting(database):
    first = post(database)
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        post(database, body="Run arbitrary system command")
    after = co.list_messages(database, recipient_label="iron-god")
    assert after["messages"][0]["body"] == first["body"]
    assert after["next_seq"] == 1


def test_acknowledgment_is_append_only_and_not_a_work_authorization(database):
    row = post(database)
    with pytest.raises(ValueError, match="RECIPIENT_MISMATCH"):
        co.ack_message(database, recipient_label="chatgpt", message_id=row["message_id"])
    ack = co.ack_message(database, recipient_label="iron-god", message_id=row["message_id"])
    repeat = co.ack_message(database, recipient_label="iron-god", message_id=row["message_id"])
    assert not ack["replayed"] and repeat["replayed"]
    assert ack["acknowledged_at_utc"] == repeat["acknowledged_at_utc"]
    assert co.list_messages(database, recipient_label="iron-god")["messages"][0]["acknowledged"]
    with sqlite3.connect(database) as db:
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_MESSAGE"):
            db.execute("UPDATE messages SET body='approval' WHERE message_id=?", (row["message_id"],))
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_RECEIPT"):
            db.execute("DELETE FROM receipts WHERE message_id=?", (row["message_id"],))


def test_every_recipient_has_independent_broadcast_receipt(database):
    row = post(database, recipient="all", kind="notification")
    assert len(co.list_messages(database, recipient_label="chatgpt")["messages"]) == 1
    assert len(co.list_messages(database, recipient_label="operator")["messages"]) == 1
    co.ack_message(database, recipient_label="chatgpt", message_id=row["message_id"])
    assert co.list_messages(database, recipient_label="chatgpt")["messages"][0]["acknowledged"]
    assert not co.list_messages(database, recipient_label="operator")["messages"][0]["acknowledged"]
    co.ack_message(database, recipient_label="operator", message_id=row["message_id"])
    assert co.list_messages(database, recipient_label="operator")["messages"][0]["acknowledged"]


def test_inboxes_are_filtered_and_cursor_is_monotonic(database):
    a = post(database, recipient="chatgpt", idempotency_key="a")
    b = post(database, recipient="iron-god", idempotency_key="b")
    c = post(database, recipient="all", idempotency_key="c")
    assert co.list_messages(database, recipient_label="chatgpt")["next_seq"] == c["seq"]
    first = co.list_messages(database, recipient_label="iron-god", limit=1)
    assert first["messages"][0]["message_id"] == b["message_id"]
    remaining = co.list_messages(database, recipient_label="iron-god", after_seq=first["next_seq"])
    assert [m["message_id"] for m in remaining["messages"]] == [c["message_id"]]
    assert co.list_messages(database, recipient_label="claude-code")["messages"][0]["message_id"] == c["message_id"]
    assert a["message_id"] not in [m["message_id"] for m in first["messages"] + remaining["messages"]]


def test_uninitialized_read_is_explicit_and_creates_no_database(database):
    result = co.list_messages(database, recipient_label="operator")
    assert result == {"status": "UNINITIALIZED", "messages": [], "next_seq": 0}
    assert not database.exists()
    with pytest.raises(ValueError, match="MESSAGE_NOT_FOUND"):
        co.ack_message(database, recipient_label="operator", message_id="a" * 32)
    assert not database.exists()


@pytest.mark.parametrize(("changes", "code"), [
    ({"sender_label": "all"}, "UNRECOGNIZED_ADVISORY_LABEL"),
    ({"recipient": "../../owner"}, "UNRECOGNIZED_ADVISORY_LABEL"),
    ({"kind": "execute_code"}, "UNRECOGNIZED_MESSAGE_KIND"),
    ({"body": " "}, "INVALID_MESSAGE_BODY"),
    ({"body": "a" * (co.MAX_MESSAGE_CHARS + 1)}, "INVALID_MESSAGE_BODY"),
    ({"body": "Bearer abc123secretvalue"}, "LIKELY_SECRET_IN_MESSAGE"),
    ({"body": "ghp_ABCDEF0123456789"}, "LIKELY_SECRET_IN_MESSAGE"),
    ({"idempotency_key": "key with space"}, "INVALID_IDEMPOTENCY_KEY"),
    ({"reference": "../C:/path to secret"}, "INVALID_MESSAGE_REFERENCE"),
])
def test_invalid_advisory_payloads_fail_closed(database, changes, code):
    with pytest.raises(ValueError, match=code):
        post(database, **changes)
    assert not database.exists()


def test_unrecognized_database_location_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="ABSOLUTE_SQLITE"):
        post(Path("relative.sqlite"))
    with pytest.raises(ValueError, match="PARENT_MISSING"):
        post(tmp_path / "missing" / "coordination.sqlite")


def test_two_writers_retry_same_key_without_creating_duplicate(database):
    def write():
        return post(database)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert results[0]["message_id"] == results[1]["message_id"]
    assert {x["replayed"] for x in results} == {True, False}
    assert len(co.list_messages(database, recipient_label="iron-god")["messages"]) == 1


def test_snapshot_is_allowlisted_and_missing_evidence_is_unknown(tmp_path):
    repo = tmp_path / "repo"
    folder = repo / "engineering"
    folder.mkdir(parents=True)
    (folder / "product-usability-gates.json").write_text(json.dumps({
        "objective": "FINISH_USABLE_PRODUCTS",
        "products": {"everward": {
            "current_state": "UNUSABLE",
            "next_gate": "FIRST_WORKING_BUILD",
            "gate": "Launch a real playable loop",
            "secret": "Never expose this",
        }},
    }), encoding="utf-8")
    view = co.targets_snapshot(repo)
    assert view["status"] == "POLICY_SNAPSHOT_NOT_LIVE_PROOF"
    assert view["products"]["everward"]["next_gate"] == "FIRST_WORKING_BUILD"
    assert "secret" not in str(view)
    state = tmp_path / "state"
    lane = state / "iron-god-products"
    lane.mkdir(parents=True)
    (lane / "lane-status.json").write_text(json.dumps({
        "status": "DISPATCHED", "repository": "jweter/knowledge-engine-ai",
        "packet_id": "P1", "secret": "NEVER_SHARE",
        "detail": "Bearer aaabbbcccsecrettoken",
    }), encoding="utf-8")
    snapshot = co.engine_snapshot(state)
    assert snapshot["sources"]["product"]["status"] == "DISPATCHED"
    assert snapshot["sources"]["canon"]["status"] == "UNAVAILABLE"
    assert "NEVER_SHARE" not in str(snapshot)
    assert "aaabbbccc" not in str(snapshot)


class FakeMCPServer:
    def __init__(self, name):
        self.name = name
        self.tools = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


def test_mcp_adapter_is_read_only_by_default_and_writes_are_opt_in(database, tmp_path, monkeypatch):
    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    server_module.MCPServer = FakeMCPServer
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)

    read_only = gateway.create_server(database=database, state_root=tmp_path,
                                     repo_root=tmp_path, client_label="chatgpt")
    assert set(read_only.tools) == {
        "get_targets", "get_engine_snapshot", "get_development_diagnostics",
        "get_engineering_evidence", "get_offload_proposals",
        "get_storage_housekeeping", "list_messages", "get_engineering_handoff", "get_message_processing", "get_handoff_assessment",
        "get_readonly_capabilities", "get_readonly_command",
        "get_notion_team_context",
    }
    assert read_only.tools["get_readonly_capabilities"]()["generic_shell"] is False
    assert read_only.tools["get_readonly_command"]("take_the_wheel")["status"] == "REFUSED"
    import project_orchestrator.notion_team_context as notion_team
    monkeypatch.setattr(
        notion_team, "safe_team_context",
        lambda *, terms, limit: {
            "status": "OK", "terms": list(terms), "limit": limit,
            "source": "NOTION_DOCUMENTS_DATABASE_ONLY", "can_execute": False,
        },
    )
    live_docs = read_only.tools["get_notion_team_context"](["team"], 2)
    assert live_docs == {
        "status": "OK", "terms": ["team"], "limit": 2,
        "source": "NOTION_DOCUMENTS_DATABASE_ONLY", "can_execute": False,
    }
    assert read_only.tools["list_messages"]()["status"] == "UNINITIALIZED"
    diagnostics = read_only.tools["get_development_diagnostics"]()
    assert diagnostics["authority"] == "READ_ONLY_NO_OWNER_GRANTS_NO_RECOVERY_EXECUTION"
    assert diagnostics["status"] == "UNVERIFIED"
    assert not (tmp_path / "observability").exists()

    writer = gateway.create_server(database=database, state_root=tmp_path,
                                   repo_root=tmp_path, client_label="claude-code",
                                   allow_advisory_writes=True)
    assert {"post_message", "ack_message"} <= set(writer.tools)
    receipt = writer.tools["post_message"]("chatgpt", "question", "Which gate next?", "question-1")
    assert read_only.tools["list_messages"]()["messages"][0]["message_id"] == receipt["message_id"]
    co.record_processing_disposition(
        database, recipient_label="chatgpt", message_id=receipt["message_id"],
        event_key="failed-once", state="RETRYABLE_FAILURE",
    )
    snapshot = read_only.tools["get_message_processing"](receipt["message_id"])
    assert snapshot["retryable_failures"] == 1 and not snapshot["acknowledged"]
    with pytest.raises(ValueError, match="RECIPIENT_MISMATCH"):
        writer.tools["get_message_processing"](receipt["message_id"])
    with pytest.raises(ValueError, match="RECIPIENT_MISMATCH"):
        writer.tools["ack_message"](receipt["message_id"])


def test_mcp_main_refuses_network_transport_without_loading_sdk(monkeypatch, capsys):
    monkeypatch.setenv("IRON_GOD_MCP_TRANSPORT", "streamable-http")
    assert gateway.main() == 2
    assert "LOCAL_STDIO_ONLY" in capsys.readouterr().err


def test_a_database_path_with_uri_metacharacters_posts_and_reads_the_same_file(tmp_path):
    """Review finding on #668: a '#' ended the SQLite URI filename, so reads opened a different file and failed."""
    for name in ("advisory#pilot.sqlite", "a%20b.sqlite", "100%.sqlite"):
        database = tmp_path / name
        co.post_message(database, sender_label="claude-code", recipient="chatgpt", kind="status", body="hi",
                           idempotency_key="k")
        got = co.list_messages(database, recipient_label="chatgpt")["messages"]
        assert [m["body"] for m in got] == ["hi"]
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".sqlite") == ["100%.sqlite", "a%20b.sqlite", "advisory#pilot.sqlite"]
    assert not (tmp_path / "advisory").exists()                                       # no stray file from a truncated name



def test_delivery_snapshot_uninitialized_is_read_only(database):
    summary = co.delivery_snapshot(database)
    assert summary == {"status": "UNINITIALIZED", "participants": {}}
    assert not database.exists()


def test_delivery_snapshot_tracks_each_receipt_without_claiming_completion(database):
    direct = post(database, recipient="chatgpt", idempotency_key="direct", body="PRIVATE_CHAT_BODY")
    broadcast = post(database, recipient="all", idempotency_key="broadcast", body="PRIVATE_BROADCAST_BODY")
    co.ack_message(database, recipient_label="chatgpt", message_id=direct["message_id"])
    summary = co.delivery_snapshot(database)
    assert summary["status"] == "OK"
    people = summary["participants"]
    assert people["chatgpt"]["addressed"] == 2
    assert people["chatgpt"]["acknowledged"] == 1
    assert people["chatgpt"]["unacknowledged"] == 1
    assert people["chatgpt"]["latest_seq"] == broadcast["seq"]
    assert people["chatgpt"]["oldest_pending_at_utc"]
    assert people["chatgpt"]["latest_acknowledged_at_utc"]
    assert people["operator"]["unacknowledged"] == 1
    assert people["claude-code"]["unacknowledged"] == 1
    assert people["iron-god"]["unacknowledged"] == 1
    co.ack_message(database, recipient_label="iron-god", message_id=broadcast["message_id"])
    after = co.delivery_snapshot(database)["participants"]
    assert after["iron-god"]["unacknowledged"] == 0
    assert after["iron-god"]["acknowledged"] == 1
    assert after["chatgpt"]["unacknowledged"] == 1
    assert "PRIVATE_CHAT_BODY" not in str(summary)
    assert "PRIVATE_BROADCAST_BODY" not in str(summary)
    assert direct["message_id"] not in str(summary)
    assert broadcast["message_id"] not in str(summary)

def test_processing_dispositions_atomic_receipt_and_replay(database):
    row = post(database)
    key = {"recipient_label": "iron-god", "message_id": row["message_id"]}
    first = co.record_processing_disposition(database, **key, event_key="retry-1", state="RETRYABLE_FAILURE")
    assert first["state"] == "RETRYABLE_FAILURE"
    assert co.list_pending_messages(database, recipient_label="iron-god")["pending_total"] == 1
    assert co.record_processing_disposition(
        database, **key, event_key="retry-1", state="RETRYABLE_FAILURE",
    )["replayed"]
    with pytest.raises(ValueError, match="PROCESSING_REPLAY_CONFLICT"):
        co.record_processing_disposition(database, **key, event_key="retry-1", state="DEFERRED")
    with pytest.raises(ValueError, match="HANDLING_REQUIRES_EVIDENCE"):
        co.record_processing_disposition(database, **key, event_key="done", state="HANDLED")
    done = co.record_processing_disposition(
        database, **key, event_key="done", state="HANDLED", evidence_reference="PR#784",
    )
    assert done["authority"] == "ADVISORY_ONLY"
    assert co.list_pending_messages(database, recipient_label="iron-god")["pending_total"] == 0
    assert co.record_processing_disposition(
        database, **key, event_key="done", state="HANDLED", evidence_reference="PR#784",
    )["replayed"]
    with pytest.raises(ValueError, match="MESSAGE_ALREADY_ACKNOWLEDGED"):
        co.record_processing_disposition(database, **key, event_key="late", state="DEFERRED")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM processing_dispositions").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1


def test_processing_disposition_recipient_isolation(database):
    row = post(database)
    with pytest.raises(ValueError, match="RECIPIENT_MISMATCH"):
        co.record_processing_disposition(
            database, recipient_label="chatgpt", message_id=row["message_id"],
            event_key="wrong", state="OBSERVED",
        )

def test_retry_snapshot_counts_unique_failures_and_isolates_broadcast_recipients(database):
    row = post(database, recipient="all")
    key = {"recipient_label": "iron-god", "message_id": row["message_id"]}
    assert co.processing_retry_snapshot(database, **key)["retryable_failures"] == 0
    for event in ("failure-1", "failure-1", "failure-2"):
        co.record_processing_disposition(database, **key, event_key=event, state="RETRYABLE_FAILURE")
    snapshot = co.processing_retry_snapshot(database, **key)
    assert snapshot["retryable_failures"] == 2
    assert not snapshot["acknowledged"]
    assert co.processing_retry_snapshot(
        database, recipient_label="chatgpt", message_id=row["message_id"],
    )["retryable_failures"] == 0
    co.record_processing_disposition(database, **key, event_key="escalation", state="ESCALATED")
    assert co.processing_retry_snapshot(database, **key)["latest_disposition"] == "ESCALATED"
    co.record_processing_disposition(
        database, **key, event_key="done", state="HANDLED", evidence_reference="PR#785",
    )
    final = co.processing_retry_snapshot(database, **key)
    assert final["acknowledged"] and final["retryable_failures"] == 2


def test_retry_snapshot_rejects_unaddressed_reader(database):
    row = post(database)
    with pytest.raises(ValueError, match="RECIPIENT_MISMATCH"):
        co.processing_retry_snapshot(database, recipient_label="chatgpt", message_id=row["message_id"])

def test_backlog_reconciliation_partitions_pending_without_receipts(database):
    diagnostic = post(database, kind="question", body="engine_status_v1",
                      reference="protocol:engine-status-v1", idempotency_key="diagnostic")
    handoff = post(database, idempotency_key="handoff")
    post(database, kind="evidence", idempotency_key="evidence")
    post(database, kind="decision_request", idempotency_key="decision")
    post(database, kind="question", recipient="all", idempotency_key="broadcast")
    post(database, recipient="chatgpt", idempotency_key="other")
    co.record_processing_disposition(
        database, recipient_label="iron-god", message_id=handoff["message_id"],
        event_key="deferred", state="DEFERRED",
    )
    before = co.backlog_reconciliation(database, recipient_label="iron-god")
    assert before["classes"] == {"SUPPORTED_DIAGNOSTIC": 1, "DEFERRED": 1,
                                 "INFORMATIONAL_REVIEW": 1, "DECISION_REVIEW": 1, "QUESTION_REVIEW": 1}
    assert before["pending_total"] == 5
    co.record_diagnostic_conflict(database, message_id=diagnostic["message_id"],
                                  reason="STATUS_REPLY_REPLAY_INVALID")
    after = co.backlog_reconciliation(database, recipient_label="iron-god")
    assert after["classes"]["DIAGNOSTIC_CONFLICT"] == 1
    assert "SUPPORTED_DIAGNOSTIC" not in after["classes"]
    assert after["pending_total"] == co.list_pending_messages(database, recipient_label="iron-god")["pending_total"]
    assert co.backlog_reconciliation(database, recipient_label="chatgpt")["pending_total"] == 2
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0


def test_backlog_reconciliation_does_not_initialize_database(database):
    assert co.backlog_reconciliation(database, recipient_label="iron-god")["status"] == "UNINITIALIZED"
    assert not database.exists()
