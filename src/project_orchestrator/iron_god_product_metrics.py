"""Meaningful product-lane metrics, computed from the lane's own evidence files.

A passing slice test, a passing repository preflight, a merged pull request and
a product feature that is actually used are different outcomes and are counted
separately. A merged change counts as product progress only when it came from
an approved roadmap backlog item and something in the product uses it: the
item changes existing product code (level >= 2), or a merged item that does
depends on it. Self-planned helpers are reported, never counted as progress.

    python -m project_orchestrator.iron_god_product_metrics [--state-root DIR] [--release-repo DIR]
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from . import iron_god_backlog as backlog


def _json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def turn_class(handback: dict[str, Any]) -> str:
    """One failure class per turn, from the worker's own summary."""
    status, summary = handback.get("status", ""), str(handback.get("summary", ""))
    if status == "PASS":
        return "passed_repository_preflight"
    if "PLAN_ALREADY_ATTEMPTED" in summary:
        return "duplicate_plan"
    if summary.startswith(("PLAN_REJECTED", "BACKLOG_SLICE_INVALID")):
        return "plan_rejected"
    if summary == "SLICE_TESTS_FAILED":
        return "slice_checks_failed"
    if summary == "canonical preflight failed":
        return "repository_preflight_failed"
    if status == "REVIEW_REQUIRED":
        return "review_required"
    return "environment_or_other"


def _started(packet_id: str) -> datetime | None:
    try:
        return datetime.strptime(packet_id.rsplit("-", 1)[-1], "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None


def compute(state_root: Path, release_repo: Path) -> dict[str, Any]:
    lane = Path(state_root) / "iron-god-products"
    turns, repairs, seconds = Counter(), Counter(), 0.0
    for path in sorted((lane / "handbacks").glob("*.json")):
        handback = _json(path, {})
        turns[turn_class(handback)] += 1
        if handback.get("repaired"):
            repairs["passed" if handback.get("status") == "PASS" else "failed"] += 1
        start, end = _started(path.stem), handback.get("observed_at")
        if start and end:
            try:
                seconds += (datetime.fromisoformat(end).replace(tzinfo=None) - start).total_seconds()
            except ValueError:
                pass
    publications = _json(lane / "publications.json", {"items": {}}).get("items", {})
    states = Counter(item.get("state") for item in publications.values())
    items, rejected = backlog.load_backlog(release_repo)
    state = backlog.load_state(lane)
    merged = {item["id"]: item for item in items if state.get("items", {}).get(item["id"], {}).get("outcome")
              == "MERGED"}
    used = {i for i, item in merged.items() if item["level"] >= 2}
    used |= {dep for item in merged.values() if item["id"] in used for dep in item["depends_on"] if dep in merged}
    accepted = sum(1 for p in publications.values() if p.get("state"))
    total_turns = sum(turns.values())
    return {
        "turns": total_turns,
        "turn_outcomes": dict(turns),
        "duplicate_planning_failures": turns["duplicate_plan"],
        "worker_repairs": dict(repairs),
        "accepted_slices": accepted,
        "passing_repository_preflight": turns["passed_repository_preflight"],
        "pull_requests": dict(states),
        "merged_pull_requests": states.get("MERGED", 0),
        "backlog": {
            "approved_open": sum(1 for i in items if i["status"] == "approved" and i["id"] not in merged),
            "proposed": sum(1 for i in items if i["status"] == "proposed"),
            "rejected_as_malformed": len(rejected),
            "roadmap_items_completed": len(merged),
            "used_by_product": len(used),
        },
        "unlinked_merges_not_counted_as_progress": states.get("MERGED", 0) - len(merged),
        "worker_minutes_per_accepted_slice": round(seconds / 60 / accepted, 1) if accepted else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Iron God product-lane metrics")
    parser.add_argument("--state-root", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", "")) / "ExponentialEngine" / "flow-state")
    parser.add_argument("--release-repo", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    print(json.dumps(compute(args.state_root, args.release_repo), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
