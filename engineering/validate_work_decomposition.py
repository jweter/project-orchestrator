from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
data=json.loads((ROOT/"engineering"/"work-decomposition.json").read_text(encoding="utf-8"))
version=data.get("schema_version")
if version not in {1,2}: raise SystemExit("work-decomposition schema_version must be 1 or 2")
if data.get("task_source_policy")!="candidate slices must be explicit and grounded in repository governing documents; engine never invents implementation tasks from prose": raise SystemExit("work decomposition must not invent tasks")
if "never duration" not in data.get("score_semantics",""): raise SystemExit("structural score semantics must prohibit prediction claims")
s=data.get("safety",{})
for key in ["active_ownership_blocks_overlap","knowledge_engine_shared_contract_requires_compatibility_clearance","product_reality_dependencies_fail_closed","specialist_lane_contract_remains_authoritative","central_merge_arbiter_remains_authoritative"]:
    if s.get(key) is not True: raise SystemExit(f"{key} must remain true")
if version == 2:
    for key in ["roadmap_importance_outranks_leverage","leverage_is_tiebreaker_not_product_direction","explicit_unlocks_must_be_authoritative"]:
        if s.get(key) is not True: raise SystemExit(f"{key} must remain true for schema v2")
print("WORK DECOMPOSITION: PASS")
