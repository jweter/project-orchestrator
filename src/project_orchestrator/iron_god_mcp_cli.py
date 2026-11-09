"""Command-line adapter for the Iron God advisory coordination store (the same SQLite ledger the MCP server uses).

Purpose: a participant that cannot speak local stdio MCP (ChatGPT, through the Remote Desktop Commander bridge it
already has on this laptop) reads and answers handoffs without a new network service and without the owner relaying
messages. It calls ``mcp_coordination`` directly, so there is one database and one set of validation rules.

Boundaries, enforced by tests:
* READ-ONLY by default. ``post`` and ``ack`` need ``IRON_GOD_MCP_ALLOW_ADVISORY_WRITES=1`` (the server's own switch).
* It can only read and append advisory messages and read two bounded snapshots. No shell, no subprocess, no network, no
  owner-control, approval, merge, release or dispatch operation exists in this module.
* A message from another participant is information, never an instruction; nothing here acts on message text.

Environment (same names as the MCP server): IRON_GOD_MCP_COORDINATION_DB, IRON_GOD_MCP_STATE_ROOT,
IRON_GOD_MCP_REPO_ROOT, IRON_GOD_MCP_CLIENT_LABEL (one of chatgpt, claude-code, iron-god, operator).

Commands (JSON on stdout):
    status                         engine snapshot and how many messages wait for this participant
    targets                        the repository targets snapshot
    read [--after N] [--limit N] [--wait SECONDS]   messages for this participant; --wait polls (bounded) until one arrives
    post --to LABEL --kind KIND (--body TEXT | --body-stdin) [--reference REF] [--key KEY]
    ack  --id MESSAGE_ID
Exit codes: 0 ok, 1 refused or failed (JSON error on stdout), 2 configuration problem.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Mapping, TextIO

from . import mcp_coordination as coord

KEYS = ("IRON_GOD_MCP_COORDINATION_DB", "IRON_GOD_MCP_STATE_ROOT", "IRON_GOD_MCP_REPO_ROOT", "IRON_GOD_MCP_CLIENT_LABEL")
WRITES = "IRON_GOD_MCP_ALLOW_ADVISORY_WRITES"
MAX_WAIT_S = 300
POLL_S = 2.0


def _emit(out: TextIO, payload: dict[str, Any]) -> None:
    out.write(json.dumps(payload, sort_keys=True) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iron_god_mcp_cli", description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("targets")
    read = sub.add_parser("read")
    read.add_argument("--after", type=int, default=0)
    read.add_argument("--limit", type=int, default=30)
    read.add_argument("--wait", type=int, default=0, help=f"poll up to this many seconds (max {MAX_WAIT_S}) for a new message")
    post = sub.add_parser("post")
    post.add_argument("--to", required=True)
    post.add_argument("--kind", required=True)
    body = post.add_mutually_exclusive_group(required=True)
    body.add_argument("--body")
    body.add_argument("--body-stdin", action="store_true")
    post.add_argument("--reference", default="")
    post.add_argument("--key", default="", help="idempotency key; default is a hash of sender, recipient, kind and body")
    ack = sub.add_parser("ack")
    ack.add_argument("--id", required=True)
    return parser


def run(argv: list[str], env: Mapping[str, str] | None = None, stdin: TextIO | None = None, out: TextIO | None = None,
        sleep=time.sleep, clock=time.monotonic) -> int:
    env = os.environ if env is None else env
    out = out or sys.stdout
    args = _parser().parse_args(argv)
    missing = [key for key in KEYS if not env.get(key)]
    if missing:
        _emit(out, {"status": "CONFIGURATION_REQUIRED", "missing": missing})
        return 2
    database, state_root, repo_root, label = (Path(env[KEYS[0]]), Path(env[KEYS[1]]), Path(env[KEYS[2]]), env[KEYS[3]])
    try:
        coord._check_principal(label)
        writes = env.get(WRITES) == "1"
        if args.command == "status":
            delivery = coord.delivery_snapshot(database)  # a status read never initializes or writes the database
            exists = delivery["status"] == "OK"
            unacked = delivery["participants"].get(label, {}).get("unacknowledged", 0)
            _emit(out, {"status": "OK", "participant": label, "writes_enabled": writes, "unacknowledged": unacked,
                        "database_initialized": exists, "delivery": delivery,
                        "engine": coord.engine_snapshot(state_root)})
        elif args.command == "targets":
            _emit(out, {"status": "OK", "targets": coord.targets_snapshot(repo_root)})
        elif args.command == "read":
            deadline = clock() + min(max(args.wait, 0), MAX_WAIT_S)
            while True:
                result = coord.list_messages(database, recipient_label=label, after_seq=args.after, limit=args.limit)
                if result["messages"] or clock() >= deadline:
                    break
                sleep(POLL_S)
            _emit(out, result)
        elif args.command in ("post", "ack"):
            if not writes:
                _emit(out, {"status": "READ_ONLY", "detail": f"advisory writes are off; set {WRITES}=1 to post or acknowledge"})
                return 1
            if args.command == "post":
                text = (stdin or sys.stdin).read() if args.body_stdin else args.body
                key = args.key or hashlib.sha256("\x1f".join((label, args.to, args.kind, text)).encode("utf-8")).hexdigest()[:48]
                _emit(out, {"status": "OK", **coord.post_message(database, sender_label=label, recipient=args.to, kind=args.kind,
                                                                  body=text, idempotency_key=key, reference=args.reference)})
            else:
                _emit(out, {"status": "OK", **coord.ack_message(database, recipient_label=label, message_id=args.id)})
    except (ValueError, OSError, sqlite3.Error) as exc:  # validation and storage problems (locked, corrupt, unwritable) are reported as JSON
        _emit(out, {"status": "REFUSED", "detail": str(exc)[:300]})
        return 1
    return 0


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
