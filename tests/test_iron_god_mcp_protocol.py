"""Official MCP SDK v2 in-process compatibility proof (when mcp extra is installed).

The existing canonical core test suite does not require a network listener.
This test is skipped if the optional MCP SDK has not been installed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from project_orchestrator.iron_god_mcp_server import create_server

mcp = pytest.importorskip("mcp", reason="optional official MCP SDK v2 not installed")


def _payload(result):
    """Official MCP v2 may supply tool dicts as JSON TextContent rather than structured_content."""
    assert not result.is_error, result
    if isinstance(result.structured_content, dict):
        return result.structured_content
    assert len(result.content) == 1 and result.content[0].type == "text", result
    return json.loads(result.content[0].text)


def test_official_mcp_client_can_exchange_and_ack_advisory_handoff(tmp_path):
    from mcp import Client

    database = tmp_path / "mcp-protocol.sqlite"
    sender = create_server(database=database, repo_root=tmp_path,
                           state_root=tmp_path, client_label="claude-code",
                           allow_advisory_writes=True)
    receiver = create_server(database=database, repo_root=tmp_path,
                             state_root=tmp_path, client_label="chatgpt",
                             allow_advisory_writes=True)

    async def exercise():
        async with Client(sender) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools.tools} == {
                "get_targets", "get_engine_snapshot", "get_development_diagnostics",
                "get_engineering_evidence", "get_offload_proposals",
                "get_storage_housekeeping", "list_messages", "get_engineering_handoff", "get_message_processing",
                "get_readonly_capabilities", "get_readonly_command", "get_handoff_assessment",
                "post_message", "ack_message", "post_engineering_handoff",
            }
            snapshot = _payload(await client.call_tool("get_storage_housekeeping", {}))
            assert snapshot["status"] == "UNAVAILABLE"
            assert snapshot["can_execute"] is False
            capabilities = _payload(await client.call_tool("get_readonly_capabilities", {}))
            assert capabilities["transport"] == "LOCAL_STDIO_ONLY"
            assert capabilities["generic_shell"] is False
            assert capabilities["can_execute"] is False
            rejected = _payload(await client.call_tool("get_readonly_command", {
                "command": "shell",
            }))
            assert rejected["status"] == "REFUSED"
            assert rejected["can_execute"] is False

            result = await client.call_tool("post_message", {
                "recipient": "chatgpt", "kind": "handoff",
                "body": "Knowledge Engine native test needs review at exact head.",
                "idempotency_key": "mcp-protocol-demo-1",
                "reference": "jweter/knowledge-engine-ai#main",
            })
            assert not result.is_error
            posted = _payload(result)
            assert isinstance(posted, dict), result
            message_id = posted["message_id"]

        async with Client(receiver) as client:
            result = await client.call_tool("list_messages", {"after_seq": 0})
            assert not result.is_error
            body = _payload(result)
            assert isinstance(body, dict) and body["messages"][0]["message_id"] == message_id
            processing = _payload(await client.call_tool("get_message_processing", {"message_id": message_id}))
            assert processing["retryable_failures"] == 0 and not processing["acknowledged"]
            acknowledge = await client.call_tool("ack_message", {"message_id": message_id})
            assert not acknowledge.is_error
            assert _payload(acknowledge)["recipient_label"] == "chatgpt"

        # Recreate the entire server/client graph, simulating independent
        # process restart without any stored session or in-memory cursor.
        fresh = create_server(database=database, repo_root=tmp_path,
                              state_root=tmp_path, client_label="chatgpt")
        async with Client(fresh) as client:
            read_only_tools = {tool.name for tool in (await client.list_tools()).tools}
            assert "get_readonly_command" in read_only_tools
            assert "get_readonly_capabilities" in read_only_tools
            assert "post_engineering_handoff" not in read_only_tools
            assert "post_message" not in read_only_tools
            assert "ack_message" not in read_only_tools
            rejected = _payload(await client.call_tool("get_readonly_command", {
                "command": "take_the_wheel",
            }))
            assert rejected["status"] == "REFUSED"
            result = await client.call_tool("list_messages", {"after_seq": 0})
            assert not result.is_error
            body = _payload(result)
            assert body["messages"][0]["acknowledged"] is True
            processing = _payload(await client.call_tool("get_message_processing", {"message_id": message_id}))
            assert processing["acknowledged"] and processing["latest_disposition"] is None

    asyncio.run(exercise())


def test_independent_stdio_processes_exchange_real_mcp_handoff(tmp_path):
    """Two different local MCP processes share durable SQLite, never the same SDK session."""
    from mcp import Client, StdioServerParameters

    repo_root = Path(__file__).resolve().parents[1]
    db = tmp_path / "stdio-handoffs.sqlite"
    base_env = {**os.environ, "PYTHONPATH": str(repo_root / "src"),
                "IRON_GOD_MCP_COORDINATION_DB": str(db),
                "IRON_GOD_MCP_REPO_ROOT": str(repo_root),
                "IRON_GOD_MCP_STATE_ROOT": str(tmp_path),
                "IRON_GOD_MCP_ALLOW_ADVISORY_WRITES": "1"}

    def config(label):
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "project_orchestrator.iron_god_mcp_server"],
            cwd=str(repo_root),
            env={**base_env, "IRON_GOD_MCP_CLIENT_LABEL": label},
        )

    async def run_two_peers():
        async with Client(config("iron-god")) as sender:
            result = await sender.call_tool("post_message", {
                "recipient": "claude-code", "kind": "handoff",
                "body": "Roadmap: one v1.0 user workflow needs real end-to-end verification",
                "idempotency_key": "stdio-two-processes-1",
                "reference": "jweter/knowledge-engine-ai#main",
            })
            message_id = _payload(result)["message_id"]

        async with Client(config("claude-code")) as receiver:
            messages = _payload(await receiver.call_tool("list_messages", {}))["messages"]
            assert len(messages) == 1 and messages[0]["message_id"] == message_id
            assert messages[0]["acknowledged"] is False
            acknowledged = _payload(await receiver.call_tool("ack_message", {
                "message_id": message_id,
            }))
            assert acknowledged["recipient_label"] == "claude-code"

        # Third subprocess, with no shared memory, confirms the receipt.
        async with Client(config("claude-code")) as reopened:
            messages = _payload(await reopened.call_tool("list_messages", {"after_seq": 0}))["messages"]
            assert len(messages) == 1 and messages[0]["acknowledged"] is True

    asyncio.run(run_two_peers())

def test_structured_handoffs_all_seven_repositories_real_stdio(tmp_path):
    from mcp import Client, StdioServerParameters
    from project_orchestrator.iron_god_observability import REPOSITORIES

    repo_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "structured.sqlite"
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src"),
           "IRON_GOD_MCP_COORDINATION_DB": str(database),
           "IRON_GOD_MCP_REPO_ROOT": str(repo_root),
           "IRON_GOD_MCP_STATE_ROOT": str(tmp_path)}

    def config(label, writes=False):
        return StdioServerParameters(
            command=sys.executable, args=["-m", "project_orchestrator.iron_god_mcp_server"],
            cwd=str(repo_root), env={**env, "IRON_GOD_MCP_CLIENT_LABEL": label,
                                     "IRON_GOD_MCP_ALLOW_ADVISORY_WRITES": "1" if writes else "0"},
        )

    async def exercise():
        ids = []
        async with Client(config("chatgpt", True)) as sender:
            for index, repository in enumerate(REPOSITORIES):
                args = {"recipient": "iron-god", "idempotency_key": f"repo-{index}",
                        "payload": {"protocol": "engineering_handoff_v1", "repository": repository,
                                    "milestone": "S2", "slice_id": f"slice-{index}",
                                    "commit_sha": "a" * 40, "status": "PROPOSED",
                                    "tests": [], "pull_request": None, "dependencies": [],
                                    "next_action": "IMPLEMENT", "failure": "NONE"}}
                first = _payload(await sender.call_tool("post_engineering_handoff", args))
                second = _payload(await sender.call_tool("post_engineering_handoff", args))
                assert first["request_id"] == second["request_id"] and second["replayed"]
                ids.append(first["request_id"])
        async with Client(config("iron-god")) as receiver:
            assert "post_engineering_handoff" not in {tool.name for tool in (await receiver.list_tools()).tools}
            for ident, repository in zip(ids, REPOSITORIES, strict=True):
                result = _payload(await receiver.call_tool("get_engineering_handoff", {"request_id": ident}))
                assert result["payload"]["repository"] == repository and not result["can_execute"]
            assert len(_payload(await receiver.call_tool("list_messages", {}))["messages"]) == 7
        async with Client(config("claude-code")) as unrelated:
            for ident in ids:
                result = await unrelated.call_tool("get_engineering_handoff", {"request_id": ident})
                assert result.is_error
        async with Client(config("iron-god")) as reopened:
            messages = _payload(await reopened.call_tool("list_messages", {}))["messages"]
            assert [message["message_id"] for message in messages] == ids
            assert not any(message["acknowledged"] for message in messages)

    asyncio.run(exercise())


def test_stdio_cli_interoperability_and_revoked_write_surface(tmp_path):
    """Independent CLI and restarted MCP sessions share one durable mailbox."""
    from mcp import Client, StdioServerParameters
    from project_orchestrator.iron_god_mcp_cli import run
    from project_orchestrator.iron_god_observability import REPOSITORIES
    from io import StringIO

    repo = Path(__file__).resolve().parents[1]
    db = tmp_path / "interop.sqlite"
    env = {**os.environ, "PYTHONPATH": str(repo / "src"),
           "IRON_GOD_MCP_COORDINATION_DB": str(db),
           "IRON_GOD_MCP_REPO_ROOT": str(repo),
           "IRON_GOD_MCP_STATE_ROOT": str(tmp_path),
           "IRON_GOD_MCP_CLIENT_LABEL": "chatgpt",
           "IRON_GOD_MCP_ALLOW_ADVISORY_WRITES": "1"}
    payload = {"protocol": "engineering_handoff_v1", "repository": REPOSITORIES[0],
               "milestone": "S2", "slice_id": "interop", "commit_sha": "a" * 40,
               "status": "PROPOSED", "tests": [], "pull_request": None,
               "dependencies": [], "next_action": "REVIEW", "failure": "NONE"}
    args = ["post-engineering-handoff", "--to", "iron-god", "--key", "interop-1"]
    out = StringIO()
    assert run(args, env=env, stdin=StringIO(json.dumps(payload)), out=out) == 0
    ident = json.loads(out.getvalue())["request_id"]

    def config(writes):
        return StdioServerParameters(
            command=sys.executable, args=["-m", "project_orchestrator.iron_god_mcp_server"],
            cwd=str(repo), env={**env, "IRON_GOD_MCP_CLIENT_LABEL": "iron-god",
                                "IRON_GOD_MCP_ALLOW_ADVISORY_WRITES": str(int(writes))})

    async def exercise():
        async with Client(config(True)) as client:
            envelope = _payload(await client.call_tool("get_engineering_handoff", {"request_id": ident}))
            assert envelope["payload"] == payload and not envelope["can_execute"]
            messages = _payload(await client.call_tool("list_messages", {}))["messages"]
            assert len(messages) == 1
            cursor = messages[0]["seq"]
        async with Client(config(False)) as client:
            assert "ack_message" not in {tool.name for tool in (await client.list_tools()).tools}
            rejected = await client.call_tool("ack_message", {"message_id": ident})
            assert rejected.is_error
            assert _payload(await client.call_tool("list_messages", {"after_seq": cursor}))["messages"] == []
            messages = _payload(await client.call_tool("list_messages", {}))["messages"]
            assert len(messages) == 1 and not messages[0]["acknowledged"]
        out = StringIO()
        assert run(args, env=env, stdin=StringIO(json.dumps(payload)), out=out) == 0
        assert json.loads(out.getvalue())["request_id"] == ident
        async with Client(config(True)) as client:
            _payload(await client.call_tool("ack_message", {"message_id": ident}))
        out = StringIO()
        assert run(["message-processing", "--id", ident],
                   env={**env, "IRON_GOD_MCP_CLIENT_LABEL": "iron-god",
                        "IRON_GOD_MCP_ALLOW_ADVISORY_WRITES": "0"}, out=out) == 0
        assert json.loads(out.getvalue())["acknowledged"]

    asyncio.run(exercise())
