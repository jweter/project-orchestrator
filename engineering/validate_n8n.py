from __future__ import annotations

from pathlib import Path

from project_orchestrator.n8n_policy import validate_workflow_directory

ROOT = Path(__file__).resolve().parents[1]
workflow_root = ROOT / "n8n" / "workflows"
validated = validate_workflow_directory(workflow_root)
print(f"N8N POLICY: PASS ({len(validated)} workflows)")
