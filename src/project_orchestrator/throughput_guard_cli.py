from __future__ import annotations

import argparse
import json
from pathlib import Path

from .fast_gate import build_repository_fast_gate_plan
from .throughput_guard import (
    build_active_work_lease,
    build_issue_ledger_event,
    classify_promotion_failure,
    compute_rework_kpis,
    evaluate_merge_lease_gate,
    execute_fast_gate,
    release_active_work_lease,
    render_issue_ledger_comment,
)


def _load(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def _print(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(prog="project-orchestrator-throughput")
    sub = parser.add_subparsers(dest="command", required=True)

    fast = sub.add_parser("fast-gate-plan")
    fast.add_argument("--repository", required=True)
    fast.add_argument("--control-plane", type=Path, required=True)
    fast.add_argument("--changed-path", action="append", dest="changed_paths", required=True)
    fast.add_argument("--execute", action="store_true")
    fast.add_argument("--repo-root", type=Path)

    classify = sub.add_parser("classify-failure")
    classify.add_argument("evidence", type=Path)

    lease_create = sub.add_parser("lease-create")
    lease_create.add_argument("evidence", type=Path)
    lease_release = sub.add_parser("lease-release")
    lease_release.add_argument("evidence", type=Path)
    lease_check = sub.add_parser("lease-check")
    lease_check.add_argument("evidence", type=Path)

    ledger = sub.add_parser("issue-ledger")
    ledger.add_argument("evidence", type=Path)
    ledger.add_argument("--markdown", action="store_true")

    kpis = sub.add_parser("rework-kpis")
    kpis.add_argument("records", type=Path)

    args = parser.parse_args()
    if args.command == "fast-gate-plan":
        control_plane = _load(args.control_plane)
        if not isinstance(control_plane, dict):
            raise ValueError("control-plane must be an object")
        result = build_repository_fast_gate_plan(
            repository=args.repository,
            control_plane=control_plane,
            changed_paths=args.changed_paths,
        )
        if args.execute:
            if args.repo_root is None:
                raise ValueError("--repo-root is required with --execute")
            result = execute_fast_gate(result, repo_root=args.repo_root)
        _print(result)
        return 0

    payload = _load(args.evidence if hasattr(args, "evidence") else args.records)
    if args.command == "classify-failure":
        if not isinstance(payload, dict):
            raise ValueError("evidence must be an object")
        _print(classify_promotion_failure(payload))
        return 0
    if args.command == "lease-create":
        if not isinstance(payload, dict):
            raise ValueError("evidence must be an object")
        _print(build_active_work_lease(**payload))
        return 0
    if args.command == "lease-release":
        if not isinstance(payload, dict) or "lease" not in payload or "released_at" not in payload:
            raise ValueError("evidence must contain lease and released_at")
        _print(release_active_work_lease(payload["lease"], released_at=payload["released_at"]))
        return 0
    if args.command == "lease-check":
        if not isinstance(payload, dict):
            raise ValueError("evidence must be an object")
        _print(evaluate_merge_lease_gate(**payload))
        return 0
    if args.command == "issue-ledger":
        if not isinstance(payload, dict):
            raise ValueError("evidence must be an object")
        event = build_issue_ledger_event(**payload)
        if args.markdown:
            print(render_issue_ledger_comment(event))
        else:
            _print(event)
        return 0
    if args.command == "rework-kpis":
        if not isinstance(payload, list):
            raise ValueError("records must be a list")
        _print(compute_rework_kpis(payload))
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
