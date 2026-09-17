from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .kpi_runtime import persistence_health, process_inbox_file
from .kpi_telemetry import (
    append_event,
    append_snapshot,
    build_rolling_reports,
    build_run_snapshot,
    load_events,
    load_snapshots,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVENT_LEDGER = ROOT / "engineering" / "kpi-events.jsonl"
DEFAULT_SNAPSHOT_LEDGER = ROOT / "engineering" / "kpi-run-snapshots.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(prog="project-orchestrator-kpi")
    parser.add_argument("--event-ledger", type=Path, default=DEFAULT_EVENT_LEDGER)
    parser.add_argument("--snapshot-ledger", type=Path, default=DEFAULT_SNAPSHOT_LEDGER)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate")

    record = sub.add_parser("record-event")
    record.add_argument("event", type=Path)

    inbox = sub.add_parser("process-inbox")
    inbox.add_argument("payload", type=Path)

    health = sub.add_parser("health")
    health.add_argument("--run-id", default=None)

    report = sub.add_parser("report")
    report.add_argument("--at", default=None, help="ISO-8601 report end; defaults to current UTC time")

    finalize = sub.add_parser("finalize-run")
    finalize.add_argument("--run-id", required=True)
    finalize.add_argument("--at", default=None, help="ISO-8601 snapshot time; defaults to current UTC time")

    args = parser.parse_args()
    if args.command == "validate":
        events = load_events(args.event_ledger)
        snapshots = load_snapshots(args.snapshot_ledger)
        print(json.dumps({"events": len(events), "snapshots": len(snapshots), "status": "VALID"}, sort_keys=True))
        return 0

    if args.command == "record-event":
        raw = json.loads(args.event.read_text(encoding="utf-8"))
        event = append_event(args.event_ledger, raw)
        print(json.dumps(event, indent=2, sort_keys=True))
        return 0

    if args.command == "process-inbox":
        result = process_inbox_file(
            args.payload,
            event_ledger=args.event_ledger,
            snapshot_ledger=args.snapshot_ledger,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "health":
        result = persistence_health(
            event_ledger=args.event_ledger,
            snapshot_ledger=args.snapshot_ledger,
            run_id=args.run_id,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] != "FAILED" else 2

    if args.command == "report":
        events = load_events(args.event_ledger)
        print(json.dumps(build_rolling_reports(events, end=args.at or _now()), indent=2, sort_keys=True))
        return 0

    if args.command == "finalize-run":
        events = load_events(args.event_ledger)
        snapshot = build_run_snapshot(events, run_id=args.run_id, generated_at=args.at or _now())
        stored = append_snapshot(args.snapshot_ledger, snapshot)
        print(json.dumps(stored, indent=2, sort_keys=True))
        return 0

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
