"""Read-only report of capable-builder usage: dispatches per rolling day against the budget, with outcomes.

The engine can count dispatches (each is one Claude Code cloud routine session) but the routine's token spend is
not reported back to it, so this reports sessions, not tokens; read the token figure from the Claude usage page.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from project_orchestrator import iron_god_capable_builder as capable  # noqa: E402


def summarize(lane_root: Path, daily_limit: int, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    rows = capable._rows(capable.dispatch_state(lane_root))
    last_day = [r for r in rows if now - float(r.get("started_at", 0)) < 24 * 3600]
    return {"daily_limit": daily_limit, "sessions_last_24h": len(last_day),
            "remaining_today": max(0, daily_limit - len(last_day)), "sessions_total": len(rows),
            "by_outcome": {kind: sum(1 for r in rows if (r.get("pr_state") or r.get("status")) == kind)
                           for kind in sorted({str(r.get("pr_state") or r.get("status")) for r in rows})}}


if __name__ == "__main__":
    root = Path(os.environ["LOCALAPPDATA"]) / "ExponentialEngine" / "flow-state" / "iron-god-products"
    print(json.dumps(summarize(root, int(sys.argv[1]) if len(sys.argv) > 1 else 4), indent=2))
