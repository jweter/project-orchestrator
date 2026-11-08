"""Opt-in local stdio MCP adapter for Iron God advisory handoffs.

Uses the official Python MCP SDK v2 (optional dependency). No supervisor,
owner-control, task dispatch, GitHub mutation or HTTP listener is exposed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import mcp_coordination as coord


def create_server(*, database: Path, state_root: Path, repo_root: Path,
                  client_label: str, allow_advisory_writes: bool = False):
    """Create a stdio-facing MCP server without starting a listener."""
    # Import lazily: normal project-orchestrator preflight needs no network SDK.
    from mcp.server import MCPServer

    coord._check_principal(client_label)
    coord._path(database)
    if not state_root.is_absolute() or not repo_root.is_absolute():
        raise ValueError("EXPLICIT_ABSOLUTE_READ_ROOTS_REQUIRED")
    server = MCPServer("Iron God Advisory Coordination")

    @server.tool()
    def get_targets() -> dict:
        """Read portfolio v1.0 goals. Reported gate states are NOT user acceptance."""
        return coord.targets_snapshot(repo_root)

    @server.tool()
    def get_engine_snapshot() -> dict:
        """Read a minimal allowlisted runtime status; NOT permission or liveness proof."""
        return coord.engine_snapshot(state_root)

    @server.tool()
    def list_messages(after_seq: int = 0, limit: int = 30) -> dict:
        """Fetch the caller's addressed advisory inbox, with stable resume cursor."""
        return coord.list_messages(database, recipient_label=client_label,
                                   after_seq=after_seq, limit=limit)

    # Local write tools are opt-in. They do not exist in read-only tool lists.
    if allow_advisory_writes:
        @server.tool()
        def post_message(recipient: str, kind: str, body: str,
                         idempotency_key: str, reference: str = "") -> dict:
            """Post inert advisory handoff only; NEVER authorize or dispatch work."""
            return coord.post_message(database, sender_label=client_label,
                                      recipient=recipient, kind=kind, body=body,
                                      idempotency_key=idempotency_key, reference=reference)

        @server.tool()
        def ack_message(message_id: str) -> dict:
            """Acknowledge receipt only, NOT execution, consent, acceptance or merge."""
            return coord.ack_message(database, recipient_label=client_label,
                                     message_id=message_id)
    return server


def main() -> int:
    if os.environ.get("IRON_GOD_MCP_TRANSPORT", "stdio") != "stdio":
        print("LOCAL_STDIO_ONLY: remote MCP requires separate authenticated rollout", file=sys.stderr)
        return 2
    keys = ("IRON_GOD_MCP_COORDINATION_DB", "IRON_GOD_MCP_STATE_ROOT",
            "IRON_GOD_MCP_REPO_ROOT", "IRON_GOD_MCP_CLIENT_LABEL")
    if any(not os.environ.get(key) for key in keys):
        print("EXPLICIT_MCP_PATHS_AND_LOCAL_CLIENT_LABEL_REQUIRED", file=sys.stderr)
        return 2
    server = create_server(
        database=Path(os.environ[keys[0]]),
        state_root=Path(os.environ[keys[1]]),
        repo_root=Path(os.environ[keys[2]]),
        client_label=os.environ[keys[3]],
        allow_advisory_writes=os.environ.get("IRON_GOD_MCP_ALLOW_ADVISORY_WRITES") == "1",
    )
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
