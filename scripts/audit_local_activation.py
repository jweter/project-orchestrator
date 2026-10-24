"""Print read-only exact-head local activation evidence for a registered portfolio."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from project_orchestrator.local_activation_audit import audit_local_activation  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio", type=Path, required=True)
    parser.add_argument("--activation", type=Path, required=True)
    parser.add_argument("--workspaces", type=Path, required=True)
    args = parser.parse_args()
    portfolio = json.loads(args.portfolio.read_text(encoding="utf-8"))
    activation = json.loads(args.activation.read_text(encoding="utf-8"))
    repositories = [
        repository["name"]
        for project in portfolio["projects"]
        for repository in project["repositories"]
    ]
    print(json.dumps(
        audit_local_activation(repositories, activation, args.workspaces),
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
