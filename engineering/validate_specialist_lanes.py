from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
path=ROOT/"engineering"/"specialist-lanes.json"
data=json.loads(path.read_text(encoding="utf-8"))

if data.get("schema_version")!=1:
    raise SystemExit("specialist-lanes schema_version must be 1")
if data.get("merge_authority")!="portfolio-orchestrator-only":
    raise SystemExit("specialist lanes must not have merge authority")
reservation=data.get("reservation",{})
if reservation.get("persistent_manual_ownership_database") is not False:
    raise SystemExit("specialist reservations must remain ephemeral/live-evidence derived")
if reservation.get("default_head_change_state")!="STALE":
    raise SystemExit("default-head changes must stale specialist lanes")
lanes=data.get("lanes",{})
if set(lanes)!={"knowledge-engine","rocksmith","everward","voicestudio"}:
    raise SystemExit("specialist lanes must match the canonical four independent product projects")
for lane_id,lane in lanes.items():
    if lane.get("max_substantial_slices_per_run")!=1:
        raise SystemExit(f"{lane_id} must remain capped at one substantial slice per run")
if lanes["knowledge-engine"].get("shared_contract_requires_compatibility_clearance") is not True:
    raise SystemExit("Knowledge Engine shared-contract specialist work must require compatibility clearance")
if "merge_pull_requests" not in data.get("specialist_forbidden",[]):
    raise SystemExit("specialist lanes must explicitly forbid merges")

print("SPECIALIST LANES: PASS")


\n