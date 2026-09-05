from __future__ import annotations

import argparse
import json
from pathlib import Path

from .decision import next_actions
from .inventory import load_inventory

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = ROOT / "engineering" / "portfolio.json"


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


def main() -> int:
    parser = argparse.ArgumentParser(prog="project-orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    if args.command == "validate":
        return validate()
    if args.command == "plan":
        return plan(args.snapshot)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
