from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
data=json.loads((ROOT/"engineering"/"work-decomposition.json").read_text(encoding="utf-8"))
if data.get("schema_version")!=1: raise SystemExit("work-decomposition schema_version must be 1")
if data.get("task_source_policy")!="candidate slices must be explicit and grounded in repository governing documents; engine never invents implementation tasks from prose": raise SystemExit("work decomposition must not invent tasks")
if "never duration" not in data.get("score_semantics",""): raise SystemExit("structural score semantics must prohibit prediction claims")
s=data.get("safety",{})
for key in ["active_ownership_blocks_overlap","knowledge_engine_shared_contract_requires_compatibility_clearance","product_reality_dependencies_fail_closed","specialist_lane_contract_remains_authoritative","central_merge_arbiter_remains_authoritative"]:
    if s.get(key) is not True: raise SystemExit(f"{key} must remain true")
print("WORK DECOMPOSITION: PASS")
