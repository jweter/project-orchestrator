"""Finalize abandoned quarantines after preserved work is durably pushed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from project_orchestrator.human_ownership import human_mode_active
from project_orchestrator.recovery_manager import (
    terminalize_abandoned_preclaim_quarantines,
)


def load_proofs(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("invalid quarantine proof schema")
    grants = payload.get("grants")
    if not isinstance(grants, dict) or not grants:
        raise ValueError("quarantine proof grants missing")
    if any(not isinstance(k, str) or not isinstance(v, dict) for k, v in grants.items()):
        raise ValueError("invalid quarantine proof entry")
    return grants


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Terminalize quarantines only after durable remote preservation proof."
    )
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    args = parser.parse_args()

    if human_mode_active(args.state_root):
        print(json.dumps({"status": "OWNER_CONTROL_ACTIVE_OR_UNVERIFIED"}))
        return 3

    proofs = load_proofs(args.proofs)
    outcomes = terminalize_abandoned_preclaim_quarantines(
        args.state_root, preserved_proofs=proofs
    )
    failed = {
        grant_id: outcomes.get(grant_id, "NO_OUTCOME")
        for grant_id in proofs
        if outcomes.get(grant_id) != "TERMINALIZED_DURABLY_PRESERVED_WORK"
    }
    result = {
        "status": "TERMINALIZED" if not failed else "PROOF_REJECTED",
        "proof_count": len(proofs),
        "failed": failed,
        "outcomes": outcomes,
    }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
