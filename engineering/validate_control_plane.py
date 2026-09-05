from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / "engineering" / "control-plane.json").read_text(encoding="utf-8"))

if data.get("schema_version") != 1:
    raise SystemExit("control-plane schema_version must be 1")
if data.get("repository") != "jweter/project-orchestrator":
    raise SystemExit("control-plane repository mismatch")
if not data.get("preflight", {}).get("required_before_pr"):
    raise SystemExit("preflight.required_before_pr must be true")
if data.get("scheduler", {}).get("never_self_disable") is not True:
    raise SystemExit("scheduler.never_self_disable must be true")
if data.get("readiness_scoring", {}).get("prohibit_guessed_percent_complete") is not True:
    raise SystemExit("guessed percent complete must remain prohibited")

print("CONTROL PLANE: PASS")
