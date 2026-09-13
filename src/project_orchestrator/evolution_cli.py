from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .evolution_engine import build_evolution_plan, evaluate_material_review_gate


def _load_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("input JSON must be an object")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project-orchestrator-evolve",
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
    else:
        result = evaluate_material_review_gate(
            current_head_sha=payload["current_head_sha"],
            reviewed_head_sha=payload["reviewed_head_sha"],
            review_state=payload["review_state"],
            findings=payload.get("findings", []),
        )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
