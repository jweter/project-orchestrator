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
                "get_targets", "get_engine_snapshot", "list_messages",
                "post_message", "ack_message",
            }
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
            acknowledge = await client.call_tool("ack_message", {"message_id": message_id})
            assert not acknowledge.is_error
            assert _payload(acknowledge)["recipient_label"] == "chatgpt"

        # Recreate the entire server/client graph, simulating independent
        # process restart without any stored session or in-memory cursor.
        fresh = create_server(database=database, repo_root=tmp_path,
                              state_root=tmp_path, client_label="chatgpt")
        async with Client(fresh) as client:
            result = await client.call_tool("list_messages", {"after_seq": 0})
            assert not result.is_error
            body = _payload(result)
            assert body["messages"][0]["acknowledged"] is True

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
