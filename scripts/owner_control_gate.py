"""Read-only fail-closed owner gate for the Windows startup launcher."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from project_orchestrator.human_ownership import human_mode_active


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", required=True)
    args = parser.parse_args()
    return 1 if human_mode_active(Path(args.state_root)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
