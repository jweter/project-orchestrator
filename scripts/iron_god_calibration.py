"""Owner-held Iron God calibration: measure what this machine's local worker can build.

Run it from the installed release while you hold control (after Give Me
Control). It refuses while a Take the Wheel grant is live, while a Canon
worker is active, or while the local model is leased. It writes
flow-state/iron-god-capability-profile.json, which the commissioning lane
requires before dispatching any slice.

    python scripts/iron_god_calibration.py [--runs 2]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT / "src"), str(ROOT)):
    if _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

from project_orchestrator import iron_god_calibration as calibration  # noqa: E402
from project_orchestrator.iron_god_canon_lane import default_lease_pool  # noqa: E402


def load_worker():
    """The exact production worker module of this release (prompt, runner, verification)."""
    spec = importlib.util.spec_from_file_location("iron_god_canon_worker", ROOT / "scripts" / "iron_god_canon_worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner-held Iron God calibration")
    parser.add_argument("--runs", type=int, default=2, help="runs per ladder level (default 2)")
    parser.add_argument("--state-root", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", "")) / "ExponentialEngine" / "flow-state")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    worker = load_worker()
    try:
        profile = calibration.run_calibration(
            args.state_root, ROOT,
            build_message=worker.worker_message, run_agent=worker.production_agent,
            verify=worker.run_verification, changed_paths=worker.changed_paths,
            lease_pool=default_lease_pool(), runs=args.runs,
        )
    except calibration.CalibrationRefused as exc:
        print(json.dumps({"status": "CALIBRATION_REFUSED", "reason": str(exc)}))
        return 3
    print(json.dumps({
        "status": "CALIBRATED", "model": profile["model"], "max_passing_level": profile["max_passing_level"],
        "recommended_worker_timeout_s": profile["recommended_worker_timeout_s"],
        "levels": {k: {"pass_rate": v["pass_rate"], "median_wall_s": v["median_wall_s"], "status": v["status"]}
                   for k, v in profile["levels"].items()},
        "profile": str(calibration.profile_path(args.state_root)),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
