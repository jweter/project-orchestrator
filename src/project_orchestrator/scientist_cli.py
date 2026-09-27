from __future__ import annotations

import argparse
import json
from pathlib import Path

from .emerging_compute import assess_compute_benchmark_result, evaluate_compute_substrates
from .engineering_scientist import (
    build_experiment_record,
    compare_hypotheses,
    distill_cross_repo_patterns,
    schedule_portfolio_resources,
    sweep_opportunities,
    validate_scientist_cycle,
)
from .lvn_dispatch import build_lvn_request, select_lvn_candidates


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _print(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-orchestrator scientist")
    sub = parser.add_subparsers(dest="command", required=True)

    for command in (
        "experiment-record",
        "compare-hypotheses",
        "resource-schedule",
        "opportunity-sweep",
        "pattern-distill",
        "validate-cycle",
        "lvn-select",
        "lvn-request",
        "compute-evaluate",
        "compute-assess",
    ):
        child = sub.add_parser(command)
        child.add_argument("input", type=Path)

    args = parser.parse_args(argv)
    payload = _load(args.input)

    if args.command == "experiment-record":
        if not isinstance(payload, dict):
            raise ValueError("input must be an object")
        _print(build_experiment_record(**payload))
        return 0
    if args.command == "compare-hypotheses":
        if not isinstance(payload, list):
            raise ValueError("input must be a list of experiment records")
        _print(compare_hypotheses(payload))
        return 0
    if args.command == "resource-schedule":
        if not isinstance(payload, dict):
            raise ValueError("input must be an object")
        _print(schedule_portfolio_resources(**payload))
        return 0
    if args.command == "opportunity-sweep":
        if not isinstance(payload, dict):
            raise ValueError("input must be an object")
        _print(sweep_opportunities(**payload))
        return 0
    if args.command == "pattern-distill":
        if not isinstance(payload, dict):
            raise ValueError("input must be an object")
        _print(distill_cross_repo_patterns(**payload))
        return 0
    if args.command == "validate-cycle":
        if not isinstance(payload, dict):
            raise ValueError("input must be an object")
        _print(validate_scientist_cycle(payload))
        return 0
    if args.command == "lvn-select":
        if not isinstance(payload, dict):
            raise ValueError("input must be an object")
        _print(select_lvn_candidates(**payload))
        return 0
    if args.command == "lvn-request":
        if not isinstance(payload, dict):
            raise ValueError("input must be an object")
        _print(build_lvn_request(**payload))
        return 0
    if args.command == "compute-evaluate":
        if not isinstance(payload, dict):
            raise ValueError("input must be an object")
        _print(evaluate_compute_substrates(**payload))
        return 0
    if args.command == "compute-assess":
        if not isinstance(payload, dict):
            raise ValueError("input must be an object")
        _print(assess_compute_benchmark_result(**payload))
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
