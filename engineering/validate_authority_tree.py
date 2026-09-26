from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"
CANONICAL = ROOT / "docs" / "EXECUTION_AUTHORITY.md"
CONTROL = ROOT / "engineering" / "control-plane.json"

ALWAYS_READ = [
    "DoWork.txt",
    "engineering/control-plane.json",
    "docs/EXECUTION_AUTHORITY.md",
]
SPECIALIST = [
    "docs/EVOLUTION_ENGINE.md",
    "docs/EVOLUTION_WORKER_HANDOFF.md",
    "docs/AGENT_ACTION_GUARD.md",
    "docs/AUTONOMOUS_ENGINEERING_SCIENTIST.md",
    "docs/LVN_PROMOTION_VERIFICATION.md",
    "docs/EMERGING_COMPUTE_EVALUATION.md",
    "docs/REPOSITORY_SKILLS.md",
    "docs/REMOTE_PREFLIGHT.md",
]

for path in [AGENTS, CLAUDE, CANONICAL, CONTROL]:
    if not path.is_file():
        raise SystemExit(f"authority-tree required file missing: {path.relative_to(ROOT)}")

agents = AGENTS.read_text(encoding="utf-8")
claude = CLAUDE.read_text(encoding="utf-8")
canonical = CANONICAL.read_text(encoding="utf-8")
control = json.loads(CONTROL.read_text(encoding="utf-8"))

for required in ALWAYS_READ:
    if required not in agents:
        raise SystemExit(f"AGENTS.md missing always-read authority: {required}")

for specialist in SPECIALIST:
    if specialist in agents or specialist in claude:
        raise SystemExit(f"bootstrap files must not preload specialist authority: {specialist}")
    if specialist not in canonical:
        raise SystemExit(f"canonical authority missing specialist reference: {specialist}")
    if not (ROOT / specialist).is_file():
        raise SystemExit(f"specialist authority missing: {specialist}")

loading = control.get("authority_loading", {})
if loading.get("always_read") != ALWAYS_READ:
    raise SystemExit("control-plane authority_loading.always_read mismatch")
if loading.get("specialist_on_demand") != SPECIALIST:
    raise SystemExit("control-plane authority_loading.specialist_on_demand mismatch")

bootstrap_bytes = len(agents.encode("utf-8")) + len(claude.encode("utf-8"))
if bootstrap_bytes > 6000:
    raise SystemExit(f"bootstrap context budget exceeded: {bootstrap_bytes} bytes")

if "Specialist mechanisms are activated on demand" not in agents:
    raise SystemExit("AGENTS.md must preserve on-demand loading rule")
if "Do not maintain a second copy of execution policy here." not in claude:
    raise SystemExit("CLAUDE.md must remain a non-duplicating bootstrap")

print(f"AUTHORITY TREE: PASS ({bootstrap_bytes} bootstrap bytes)")
