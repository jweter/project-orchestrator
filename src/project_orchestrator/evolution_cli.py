from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .evolution_bridge import (
    build_worker_handoff,
    execute_worker_safe_repair,
    record_verified_repair_lesson,
)
from .evolution_engine import build_evolution_plan, evaluate_material_review_gate


def _load_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("input JSON must be an object")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project-orchestrator evolve",
        description="Build governed Fix_It-style engineering evolution plans from verified evidence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Build an Evolution Engine plan.")
    plan.add_argument(
        "--input", required=True, help="Path to an Evolution Engine input JSON file."
    )

    review = sub.add_parser(
        "review-gate",
        help="Evaluate whether current-head review evidence permits merge.",
    )
    review.add_argument(
        "--input", required=True, help="Path to a review-gate input JSON file."
    )

    worker = sub.add_parser(
        "worker-handoff",
        help="Route a sanitized unattended-worker result into repair/evolution next action.",
    )
    worker.add_argument("--input", required=True, help="Path to worker handoff JSON.")
    worker.add_argument(
        "--execute-safe-repair",
        action="store_true",
        help="Execute only an already-authorized deterministic safe repair.",
    )
    worker.add_argument(
        "--repo-root",
        help="Local repository root; required with --execute-safe-repair.",
    )

    lesson = sub.add_parser(
        "record-verified-lesson",
        help="Append an independently verified repair lesson to in-memory JSON output.",
    )
    lesson.add_argument("--input", required=True, help="Path to verified lesson JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    payload = _load_json(args.input)

    if args.command == "plan":
        result = build_evolution_plan(
            project=payload["project"],
            repository=payload["repository"],
            pattern_id=payload["pattern_id"],
            memory=payload["memory"],
            events=payload["events"],
            measurements=payload.get("measurements", []),
            minimum_evolution_sample_count=payload.get(
                "minimum_evolution_sample_count", 2
            ),
        )
    elif args.command == "review-gate":
        result = evaluate_material_review_gate(
            current_head_sha=payload["current_head_sha"],
            reviewed_head_sha=payload["reviewed_head_sha"],
            review_state=payload["review_state"],
            findings=payload.get("findings", []),
        )
    elif args.command == "worker-handoff":
        result = build_worker_handoff(
            worker_result=payload["worker_result"],
            control_plane=payload.get("control_plane", {}),
            memory=payload["memory"],
            prior_events=payload.get("prior_events", []),
            measurements=payload.get("measurements", []),
            repair_iteration=payload.get("repair_iteration", 1),
        )
        if args.execute_safe_repair:
            if not args.repo_root:
                raise ValueError("--repo-root is required with --execute-safe-repair")
            result = execute_worker_safe_repair(result, repo_root=Path(args.repo_root))
    else:
        result = record_verified_repair_lesson(
            memory=payload["memory"],
            project=payload["project"],
            repository=payload["repository"],
            source_ref=payload["source_ref"],
            symptom=payload["symptom"],
            root_cause=payload["root_cause"],
            fix=payload["fix"],
            verification=payload["verification"],
            regression_protection=payload["regression_protection"],
            residual_risk=payload["residual_risk"],
        )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
