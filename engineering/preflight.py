from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "preflight-evidence.json"


def git_head(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "UNKNOWN"

checks = [
    ("control_plane", [sys.executable, "engineering/validate_control_plane.py"]),
    ("portfolio_inventory", [sys.executable, "engineering/validate_portfolio.py"]),
    ("autonomy_loop", [sys.executable, "-m", "project_orchestrator.loop_audit", "engineering/autonomy-loop-contract.json"]),
    ("harness_context_audit", [sys.executable, "-m", "project_orchestrator.harness_audit", "--policy", "engineering/harness-audit.json"]),
    ("agent_eval_suite", [sys.executable, "-m", "project_orchestrator.agent_eval", "validate-suite", "engineering/agent-eval-suite.json"]),
    ("agent_eval_runner_config", [sys.executable, "-m", "project_orchestrator.agent_eval_runner", "validate-config", "engineering/agent-eval-runner.example.json"]),
    ("specialist_lanes", [sys.executable, "engineering/validate_specialist_lanes.py"]),
    ("work_decomposition", [sys.executable, "engineering/validate_work_decomposition.py"]),
    ("regression_prevention", [sys.executable, "-m", "project_orchestrator.cli", "regression-prevention", "--events", "engineering/regression-prevention-smoke-events.json", "--policies", "engineering/regression-prevention-smoke-policies.json"]),
    ("throughput_optimizer", [sys.executable, "-m", "project_orchestrator.cli", "throughput-optimize"]),
    ("learning_memory", [sys.executable, "engineering/validate_learning_memory.py"]),
    ("repository_skills", [sys.executable, "-m", "project_orchestrator.skill_extraction", "validate"]),
    ("compounding_timeseries", [sys.executable, "engineering/validate_timeseries.py"]),
    ("tooling_capabilities", [sys.executable, "engineering/validate_tooling_capabilities.py"]),
    ("n8n_policy", [sys.executable, "engineering/validate_n8n.py"]),
    ("compile", [sys.executable, "-m", "compileall", "-q", "src", "tests", "engineering"]),
    ("tests", [sys.executable, "-m", "pytest", "-q"]),
    ("cli_validate", [sys.executable, "-m", "project_orchestrator.cli", "validate"]),
    ("reinvestment_plan_smoke", [sys.executable, "-m", "project_orchestrator.cli", "reinvestment-plan"]),
]

results = []
ok = True
for name, argv in checks:
    completed = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True)
    results.append({
        "name": name,
        "argv": argv,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    })
    if completed.returncode != 0:
        ok = False
        break

EVIDENCE.write_text(json.dumps({
    "schema_version": 2,
    "repository": "jweter/project-orchestrator",
    "head_sha": git_head(ROOT),
    "mode": "FULL",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "status": "GREEN" if ok else "FAILED",
    "results": results,
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

for result in results:
    print(f"{result['name']}: {'PASS' if result['returncode'] == 0 else 'FAIL'}")
    if result["returncode"]:
        print(result["stdout"])
        print(result["stderr"], file=sys.stderr)

raise SystemExit(0 if ok else 1)
