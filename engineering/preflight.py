from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "preflight-evidence.json"

checks = [
    ("control_plane", [sys.executable, "engineering/validate_control_plane.py"]),
    ("portfolio_inventory", [sys.executable, "engineering/validate_portfolio.py"]),
    ("compile", [sys.executable, "-m", "compileall", "-q", "src", "tests", "engineering"]),
    ("tests", [sys.executable, "-m", "pytest", "-q"]),
    ("cli_validate", [sys.executable, "-m", "project_orchestrator.cli", "validate"]),
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
    "schema_version": 1,
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
