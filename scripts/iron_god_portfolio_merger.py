"""Run the autonomous portfolio merger once.

``--dry-run`` only reads GitHub and prints what the merger would do (no promotion, merge or ledger write); it is safe to
run at any time. A live pass changes GitHub, so it runs only while owner control is AUTHORIZED, exactly like the
independent PR controller, and writes durable evidence under ``<state-root>/portfolio-merger/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
for _root in (str(_repo_root), str(_repo_root / "src")):
    if _root not in sys.path:
        sys.path.insert(0, _root)

from project_orchestrator import iron_god_portfolio_merger as portfolio_merger  # noqa: E402
from project_orchestrator.human_ownership import owner_control_status  # noqa: E402


def run(state_root: Path | None, dry_run: bool) -> tuple[int, dict]:
    if not dry_run:
        if state_root is None:
            return 2, {"status": "STATE_ROOT_REQUIRED", "acted": False}
        status = owner_control_status(state_root)
        if status != "AUTHORIZED":
            return 2, {"status": status, "acted": False}
    def guard() -> str | None:  # re-checked before every promotion and merge, like the controller's per-command veto
        if dry_run or state_root is None:
            return None
        status = owner_control_status(state_root)
        return None if status == "AUTHORIZED" else status

    rows = portfolio_merger.cycle(state_root or Path("."), dry_run=dry_run, guard=guard)
    failed = [r for r in rows if r.get("decision") in {"REPOSITORY_QUERY_FAILED", "PR_PROCESSING_FAILED", "MERGE_FAILED"}]
    return (1 if failed else 0), {"status": "DRY_RUN" if dry_run else "RECONCILED", "acted": not dry_run, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    code, payload = run(args.state_root.resolve() if args.state_root else None, args.dry_run)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
