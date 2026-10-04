from __future__ import annotations

import argparse
import json
from pathlib import Path

from .learning import load_memory
from .shared_memory import query_all_memory, sync_shared_memory

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEARNING_MEMORY = ROOT / "engineering" / "state" / "learning-memory.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-orchestrator shared-memory")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser("sync")
    sync_parser.add_argument("--root", type=Path, default=None)

    query_parser = sub.add_parser("query")
    query_parser.add_argument("--root", type=Path, default=None)
    query_parser.add_argument("--project", default=None)
    query_parser.add_argument("--repository", default=None)
    query_parser.add_argument("--source-type", default=None)
    query_parser.add_argument("--term", action="append", dest="terms")
    query_parser.add_argument("--limit", type=int, default=8)

    args = parser.parse_args(argv)
    if args.command == "sync":
        memory = load_memory(DEFAULT_LEARNING_MEMORY)
        print(json.dumps(sync_shared_memory(memory, args.root), indent=2, sort_keys=True))
        return 0

    print(
        json.dumps(
            query_all_memory(
                root=args.root,
                project=args.project,
                repository=args.repository,
                source_type=args.source_type,
                terms=args.terms,
                limit=args.limit,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
