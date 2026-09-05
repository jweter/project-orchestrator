from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compatibility import load_contract, plan_change
from .decision import next_actions
from .evidence_collector import COLLECTIBLE_REPOSITORIES, collect_portfolio_windows
from .github_evidence import UrllibGitHubTransport, collect_portfolio
from .inventory import load_inventory
from .learning import add_lesson, load_memory, write_memory
from .reinvestment import plan_from_store
from .required_checks import resolve_required_checks
from .timeseries import (
    append_window,
    build_reinvestment_snapshot,
    load_store,
    recent_windows,
    write_store,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = ROOT / "engineering" / "portfolio.json"
DEFAULT_KE_COMPATIBILITY = ROOT / "engineering" / "knowledge-engine-compatibility.json"
DEFAULT_LEARNING_MEMORY = ROOT / "engineering" / "learning-memory.json"
DEFAULT_TIMESERIES_STORE = ROOT / "engineering" / "compounding-timeseries.json"


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


def timeseries_validate() -> int:
    store = load_store(DEFAULT_TIMESERIES_STORE)
    print(f"compounding timeseries valid: {len(store['windows'])} evidence windows")
    return 0


def timeseries_record(window_path: Path) -> int:
    window = json.loads(window_path.read_text(encoding="utf-8"))
    store = load_store(DEFAULT_TIMESERIES_STORE)
    updated = append_window(store, window)
    write_store(DEFAULT_TIMESERIES_STORE, updated)
    print(f"compounding timeseries contains {len(updated['windows'])} evidence windows")
    return 0


def timeseries_report(
    since: str | None,
    project: str | None,
    repository: str | None,
    reinvestment: bool,
) -> int:
    store = load_store(DEFAULT_TIMESERIES_STORE)
    if reinvestment:
        print(json.dumps(build_reinvestment_snapshot(store, since=since), indent=2, sort_keys=True))
        return 0
    windows = recent_windows(store, since=since, project=project, repository=repository)
    print(json.dumps(windows, indent=2, sort_keys=True))
    return 0


def reinvestment_plan(since: str | None) -> int:
    store = load_store(DEFAULT_TIMESERIES_STORE)
    result = plan_from_store(store, since=since)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def collect_evidence(repository: str | None, since: str | None, dry_run: bool) -> int:
    transport = UrllibGitHubTransport.from_environment()
    store = load_store(DEFAULT_TIMESERIES_STORE)
    repositories = [repository] if repository else None
    windows = collect_portfolio_windows(transport, store, repositories=repositories, since=since)
    if not windows:
        print("no new merged pull requests found; nothing to record")
        return 0
    updated = store
    for window in windows:
        updated = append_window(updated, window)
    if dry_run:
        print(json.dumps(windows, indent=2, sort_keys=True))
        print(f"dry run: {len(windows)} new evidence window(s) not written")
        return 0
    write_store(DEFAULT_TIMESERIES_STORE, updated)
    print(f"recorded {len(windows)} new evidence window(s); store now has {len(updated['windows'])} total")
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
    memory_add_parser = sub.add_parser("memory-add")
    memory_add_parser.add_argument("lesson", type=Path)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--output", type=Path)
    collect_parser.add_argument(
        "--resolve-required-checks",
        action="store_true",
        help="resolve current required CI from live branch policy and control-plane evidence",
    )
    sub.add_parser("timeseries-validate")
    timeseries_record_parser = sub.add_parser("timeseries-record")
    timeseries_record_parser.add_argument("window", type=Path)
    timeseries_report_parser = sub.add_parser("timeseries-report")
    timeseries_report_parser.add_argument("--since", default=None, help="ISO-8601 cutoff; only windows ending on/after this are included")
    timeseries_report_parser.add_argument("--project", default=None)
    timeseries_report_parser.add_argument("--repository", default=None)
    timeseries_report_parser.add_argument(
        "--reinvestment",
        action="store_true",
        help="print the aggregated snapshot shape the future Reinvestment Controller will consume",
    )
    reinvestment_plan_parser = sub.add_parser("reinvestment-plan")
    reinvestment_plan_parser.add_argument("--since", default=None, help="ISO-8601 cutoff; only windows ending on/after this are considered")
    collect_evidence_parser = sub.add_parser("collect-evidence")
    collect_evidence_parser.add_argument(
        "--repository", default=None, choices=sorted(COLLECTIBLE_REPOSITORIES),
        help="collect only this repository; default is all portfolio repositories",
    )
    collect_evidence_parser.add_argument(
        "--since", default=None,
        help="ISO-8601 cutoff; default is the latest previously-collected window's end, or a 30-day lookback if none exists",
    )
    collect_evidence_parser.add_argument(
        "--dry-run", action="store_true", help="print the windows that would be recorded without writing them",
    )
    args = parser.parse_args()
    if args.command == "validate":
        return validate()
    if args.command == "plan":
        return plan(args.snapshot)
    if args.command == "compat-plan":
        return compatibility_plan(args.change)
    if args.command == "memory-validate":
        return memory_validate()
    if args.command == "memory-add":
        return memory_add(args.lesson)
    if args.command == "collect":
        return collect(args.output, args.resolve_required_checks)
    if args.command == "timeseries-validate":
        return timeseries_validate()
    if args.command == "timeseries-record":
        return timeseries_record(args.window)
    if args.command == "timeseries-report":
        return timeseries_report(args.since, args.project, args.repository, args.reinvestment)
    if args.command == "reinvestment-plan":
        return reinvestment_plan(args.since)
    if args.command == "collect-evidence":
        return collect_evidence(args.repository, args.since, args.dry_run)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
