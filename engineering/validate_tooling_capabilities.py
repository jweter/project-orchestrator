from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "engineering" / "tooling-capabilities.json"
data = json.loads(path.read_text(encoding="utf-8"))

if data.get("schema_version") != 1:
    raise SystemExit("tooling-capabilities schema_version must be 1")

policy = data.get("policy")
if not isinstance(policy, dict):
    raise SystemExit("tooling-capabilities policy must be an object")
for required_flag in (
    "prefer_official_vendor_tools",
    "least_privilege",
    "secrets_must_not_be_committed",
    "private_product_reality_payloads_must_not_be_committed",
    "paid_or_meaningful_recurring_cost_requires_jeremy",
    "tool_availability_must_be_verified_not_assumed",
):
    if policy.get(required_flag) is not True:
        raise SystemExit(f"tooling policy flag must remain true: {required_flag}")

capabilities = data.get("capabilities")
if not isinstance(capabilities, list) or not capabilities:
    raise SystemExit("tooling-capabilities capabilities must be a non-empty list")

ids: set[str] = set()
for capability in capabilities:
    if not isinstance(capability, dict):
        raise SystemExit("every tooling capability must be an object")
    capability_id = capability.get("id")
    if not isinstance(capability_id, str) or not capability_id:
        raise SystemExit("every tooling capability requires a non-empty id")
    if capability_id in ids:
        raise SystemExit(f"duplicate tooling capability id: {capability_id}")
    ids.add(capability_id)
    if not isinstance(capability.get("status"), str):
        raise SystemExit(f"tooling capability {capability_id} requires status")

for required_id in (
    "github_mcp",
    "playwright_mcp",
    "n8n_orchestration",
    "hypothesis",
    "semgrep_community",
    "trivy",
    "dependabot",
):
    if required_id not in ids:
        raise SystemExit(f"required tooling capability missing: {required_id}")

print("TOOLING CAPABILITIES: PASS")
