"""Run the independent freeze flight recorder (opt-in; read-only).  Stop it with Ctrl+C or by ending the process.

    python scripts/iron_god_flight_recorder.py [--interval 5] [--path FILE]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from project_orchestrator import iron_god_flight_recorder as fr  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=fr.DEFAULT_INTERVAL_S)
    parser.add_argument("--path", type=Path, default=None)
    args = parser.parse_args()
    try:
        fr.run(args.path, args.interval)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
