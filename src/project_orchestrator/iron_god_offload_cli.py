"""Bounded local CLI for the advisory ChatGPT offload pilot.

No model, worker, Git mutation, owner-control action or network listener.
Writes are opt-in via the *existing* advisory mailbox switch; an advisory
reservation is not an authenticated worker claim or code execution grant.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, TextIO

from . import iron_god_offload as offload

DB_ENV = "IRON_GOD_MCP_COORDINATION_DB"
STAGE_ENV = "IRON_GOD_OFFLOAD_STAGE_ROOT"
WRITE_ENV = "IRON_GOD_MCP_ALLOW_ADVISORY_WRITES"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iron_god_offload_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("list", help="Read advisory offload proposals; no SQLite mutation")
    view.add_argument("--limit", type=int, default=12)
    show = commands.add_parser("show", help="Read one exact advisory offer with its path fence")
    show.add_argument("--id", required=True)
    claim = commands.add_parser("claim", help="Reserve one existing offer for ChatGPT; not a worker lease")
    claim.add_argument("--id", required=True)
    seal = commands.add_parser("seal", help="Read a bounded patch from stdin, seal and register it")
    seal.add_argument("--id", required=True)
    return parser


def _emit(out: TextIO, value: dict[str, Any]) -> None:
    out.write(json.dumps(value, sort_keys=True) + "\n")


def run(
    argv: list[str], *, env: Mapping[str, str] | None = None,
    out: TextIO | None = None, patch_input: bytes | None = None,
) -> int:
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    args = _parser().parse_args(argv)
    source = env.get(DB_ENV)
    if not source or not Path(source).is_absolute():
        _emit(out, {"status": "CONFIGURATION_REQUIRED", "missing": DB_ENV})
        return 2
    db_path = Path(source)
    if args.command in {"claim", "seal"} and env.get(WRITE_ENV) != "1":
        _emit(out, {"status": "READ_ONLY", "execution_authorized": False})
        return 1
    try:
        if args.command == "list":
            result = offload.list_offload_proposals(db_path, limit=args.limit)
        elif args.command == "show":
            result = offload.read_offer(db_path, args.id)
        elif args.command == "claim":
            result = offload.claim_slice(db_path, args.id)
        elif args.command == "seal":
            path = env.get(STAGE_ENV)
            if not path or not Path(path).is_absolute():
                _emit(out, {"status": "CONFIGURATION_REQUIRED", "missing": STAGE_ENV})
                return 2
            offer = offload.read_offer(db_path, args.id)
            patch = (sys.stdin.buffer.read(offload.MAX_PATCH_BYTES + 1)
                     if patch_input is None else patch_input)
            candidate = offload.seal_candidate(offer, patch, Path(path))
            receipt = offload.register_candidate(db_path, candidate)
            result = {
                "status": receipt["status"],
                "offer_id": receipt["offer_id"],
                "patch_sha256": candidate["patch_sha256"],
                "manifest_sha256": candidate["manifest_sha256"],
                "changed_paths": candidate["changed_paths"],
                "execution_authorized": False,
            }
        else:
            raise AssertionError("unknown command")
    except (offload.OffloadRejected, OSError, ValueError) as exc:
        # Never include untrusted paths, patch contents or exception text.
        code = (str(exc) if isinstance(exc, offload.OffloadRejected) else
                type(exc).__name__.upper())
        _emit(out, {"status": "REFUSED", "code": code[:80], "execution_authorized": False})
        return 1
    _emit(out, {"status": "OK", "result": result, "execution_authorized": False})
    return 0


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
