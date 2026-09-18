from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any


VALID_STATES = {"USER_ACTIVE", "IDLE_AVAILABLE", "EXECUTING", "COOLDOWN", "OFFLINE"}


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("state") not in VALID_STATES:
            raise ValueError("invalid actor telemetry state")
        datetime.fromisoformat(event["timestamp_utc"])
        events.append(event)
    return events


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {state: 0 for state in sorted(VALID_STATES)}
    green_cycles = 0
    cooldowns = 0
    backoffs: list[float] = []
    for event in events:
        counts[event["state"]] += 1
        if event.get("result") == "bridge_cycle_green":
            green_cycles += 1
        if event["state"] == "COOLDOWN":
            cooldowns += 1
            if isinstance(event.get("backoff_seconds"), (int, float)):
                backoffs.append(float(event["backoff_seconds"]))
    return {
        "schema_version": 1,
        "event_count": len(events),
        "state_counts": counts,
        "green_bridge_cycles": green_cycles,
        "cooldown_count": cooldowns,
        "max_observed_backoff_seconds": max(backoffs) if backoffs else None,
        "latest_state": events[-1]["state"] if events else "OFFLINE",
        "latest_timestamp_utc": events[-1]["timestamp_utc"] if events else None,
    }
