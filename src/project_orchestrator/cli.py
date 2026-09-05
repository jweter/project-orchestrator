from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compatibility import load_contract, plan_change
from .compounding import add_window, evaluate_compounding, load_series, write_series
from .decision import next_actions
from .github_evidence import UrllibGitHubTransport, collect_portfolio
from .inventory import load_inventory
from .learning import add_lesson, load_memory, write_memory
from .required_checks import resolve_required_checks

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = ROOT / "engineering" / "portfolio.json"
DEFAULT_KE_COMPATIBILITY = ROOT / "engineering" / "knowledge-engine-compatibility.json"
DEFAULT_LEARNING_MEMORY = ROOT / "engineering" / "learning-memory.json"
DEFAULT_COMPOUNDING_SERIES = ROOT / "engineering" / "compounding-series.json"


def validate() -> int:
    inventory = load_inventory(DEFAULT_INVENTORY)
    count = sum(len(project["repositories"]) for project in inventory["projects"])
    print(f"portfolio inventory valid: {count} repositories")
    return 0


def plan(snapshot_path: Path) -> int:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    actions = next_actions(snapshot)
    print(json.dumps([action.__dict__ for action in actions], indent=2, sort_keys=True))
    return 0


def collect(output: Path | None, resolve_checks: bool) -> int:
    transport = UrllibGitHubTransport.from_environment()
    snapshot = collect_portfolio(str(DEFAULT_INVENTORY), transport)
    if resolve_checks:
        snapshot = resolve_required_checks(snapshot, transport)
    rendered = json.dumps(snapshot, indent=2, sort_keys=True)
    if output is None:
        print(rendered)
    else:
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote raw live GitHub evidence to {output}")
    return 0


def compatibility_plan(change_path: Path) -> int:
    change = json.loads(change_path.read_text(encoding="utf-8"))
    contract = load_contract(DEFAULT_KE_COMPATIBILITY)
    print(json.dumps(plan_change(change, contract), indent=2, sort_keys=True))
    return 0


def memory_validate() -> int:
    memory = load_memory(DEFAULT_LEARNING_MEMORY)
    print(f"learning memory valid: {len(memory['entries'])} verified lessons")
    return 0


def memory_add(lesson_path: Path) -> int:
    lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
    memory = load_memory(DEFAULT_LEARNING_MEMORY)
    updated = add_lesson(memory, lesson)
    write_memory(DEFAULT_LEARNING_MEMORY, updated)
    print(f"learning memory contains {len(updated['entries'])} verified lessons")
    return 0


def compounding_validate() -> int:
    series = load_series(DEFAULT_COMPOUNDING_SERIES)
    print(f"compounding series valid: {len(series['windows'])} verified windows")
    return 0


def compounding_evaluate() -> int:
    print(json.dumps(evaluate_compounding(load_series(DEFAULT_COMPOUNDING_SERIES)), indent=2, sort_keys=True))
    return 0


def compounding_add(window_path: Path) -> int:
    window = json.loads(window_path.read_text(encoding="utf-8"))
    series = load_series(DEFAULT_COMPOUNDING_SERIES)
    updated = add_window(series, window)
    write_series(DEFAULT_COMPOUNDING_SERIES, updated)
    print(f"compounding series contains {len(updated['windows'])} verified windows")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="project-orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("snapshot", type=Path)
    compatibility_parser = sub.add_parser("compat-plan")
    compatibility_parser.add_argument("change", type=Path)
    sub.add_parser("memory-validate")
    sub.add_parser("compounding-validate")
    sub.add_parser("compounding-evaluate")
    compounding_add_parser = sub.add_parser("compounding-add")
    compounding_add_parser.add_argument("window", type=Path)
    memory_add_parser = sub.add_parser("memory-add")
    memory_add_parser.add_argument("lesson", type=Path)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--output", type=Path)
    collect_parser.add_argument(
        "--resolve-required-checks",
        action="store_true",
        help="resolve current required CI from live branch policy and control-plane evidence",
    )
    args = parser.parse_args()
    if args.command == "validate":
        return validate()
    if args.command == "plan":
        return plan(args.snapshot)
    if args.command == "compat-plan":
        return compatibility_plan(args.change)
    if args.command == "compounding-validate":
        return compounding_validate()
    if args.command == "compounding-evaluate":
        return compounding_evaluate()
    if args.command == "compounding-add":
        return compounding_add(args.window)
    if args.command == "memory-validate":
        return memory_validate()
    if args.command == "memory-add":
        return memory_add(args.lesson)
    if args.command == "collect":
        return collect(args.output, args.resolve_required_checks)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
