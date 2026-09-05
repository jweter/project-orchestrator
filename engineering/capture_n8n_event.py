from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from project_orchestrator.n8n_policy import (
    FAILURE_CONCLUSIONS,
    KNOWLEDGE_ENGINE_REPOSITORIES,
    PORTFOLIO_REPOSITORIES,
)

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_REASONS = {"ci_failure", "knowledge_engine_main_change"}
_ALLOWED_CONCLUSIONS = FAILURE_CONCLUSIONS | {""}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--source-pr", default="")
    parser.add_argument("--source-head-sha", required=True)
    parser.add_argument("--source-conclusion", default="")
    parser.add_argument("--source-check-suite-id", default="")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.source_repository not in PORTFOLIO_REPOSITORIES:
        raise SystemExit("source repository is outside the canonical portfolio")
    if not _SHA40.fullmatch(args.source_head_sha):
        raise SystemExit("source head SHA must be 40 lowercase hexadecimal characters")
    if args.source_pr and not args.source_pr.isdigit():
        raise SystemExit("source PR must be empty or numeric")
    if args.source_check_suite_id and not args.source_check_suite_id.isdigit():
        raise SystemExit("check suite id must be empty or numeric")
    if args.reason not in _ALLOWED_REASONS:
        raise SystemExit("unsupported n8n event reason")
    if args.source_conclusion not in _ALLOWED_CONCLUSIONS:
        raise SystemExit("unsupported source conclusion")
    if args.reason == "ci_failure" and not args.source_pr:
        raise SystemExit("ci_failure evidence requires a PR number")
    if args.reason == "ci_failure" and args.source_conclusion not in FAILURE_CONCLUSIONS:
        raise SystemExit("ci_failure evidence requires a terminal failure conclusion")
    if args.reason == "knowledge_engine_main_change":
        if args.source_repository not in KNOWLEDGE_ENGINE_REPOSITORIES:
            raise SystemExit("Knowledge Engine change must originate from the Knowledge Engine family")
        if args.source_pr:
            raise SystemExit("Knowledge Engine main-change event must not invent a PR number")

    state = (
        "REPAIR_CANDIDATE"
        if args.reason == "ci_failure"
        else "DEPENDENCY_RECHECK_CANDIDATE"
    )
    payload = {
        "schema_version": 1,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "source": "n8n",
        "state": state,
        "reason": args.reason,
        "repository": args.source_repository,
        "pr_number": int(args.source_pr) if args.source_pr else None,
        "head_sha": args.source_head_sha,
        "conclusion": args.source_conclusion or None,
        "check_suite_id": (
            int(args.source_check_suite_id) if args.source_check_suite_id else None
        ),
        "authority": "evidence_only_not_merge_authority",
    }
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"captured {state} evidence for {args.source_repository}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
