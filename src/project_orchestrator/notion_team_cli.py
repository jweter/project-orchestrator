"""On-demand Iron God Notion Documents reads without token arguments or writes."""
from __future__ import annotations

import argparse
import json

from .notion_team_context import MAX_RETURNED_DOCUMENTS, safe_team_context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-orchestrator notion-team")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Check the existing local token and Documents access")
    recent = sub.add_parser("recent", help="Read current approved Documents pages")
    recent.add_argument("--limit", type=int, default=5)
    search = sub.add_parser("search", help="Search live approved Documents page contents")
    search.add_argument("--term", action="append", dest="terms", required=True)
    search.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)
    limit = 1 if args.command == "status" else args.limit
    if not 1 <= limit <= MAX_RETURNED_DOCUMENTS:
        parser.error("limit must be between 1 and 6")
    terms = args.terms if args.command == "search" else ()
    result = safe_team_context(terms=terms, limit=limit)
    if args.command == "status":
        result = {key: val for key, val in result.items() if key != "items"}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in {"OK", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
