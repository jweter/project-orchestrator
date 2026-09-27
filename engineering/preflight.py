from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "preflight-evidence.json"
SRC = ROOT / "src"
PREFLIGHT_ENV = os.environ.copy()
PREFLIGHT_ENV["PYTHONPATH"] = str(SRC) + os.pathsep + PREFLIGHT_ENV.get("PYTHONPATH", "")
DEFAULT_CHECK_TIMEOUT_SECONDS = int(os.environ.get("PREFLIGHT_CHECK_TIMEOUT_SECONDS", "180"))
TEST_CHECK_TIMEOUT_SECONDS = int(os.environ.get("PREFLIGHT_TEST_TIMEOUT_SECONDS", "900"))


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
    ("control_surface", [sys.executable, "engineering/validate_control_surface.py"]),
    ("supervisor_architecture", [sys.executable, "engineering/validate_supervisor_architecture.py"]),
    ("builder_architecture", [sys.executable, "engineering/validate_builder_architecture.py"]),
    ("authority_tree", [sys.executable, "engineering/validate_authority_tree.py"]),
    ("portfolio_inventory", [sys.executable, "engineering/validate_portfolio.py"]),
    ("autonomy_loop", [sys.executable, "-m", "project_orchestrator.loop_audit", "engineering/autonomy-loop-contract.json"]),
    ("harness_context_audit", [sys.executable, "-m", "project_orchestrator.harness_audit", "--policy", "engineering/harness-audit.json"]),
    ("harness_security", [sys.executable, "-m", "project_orchestrator.harness_security", "--policy", "engineering/harness-security.json"]),
    ("agent_eval_suite", [sys.executable, "-m", "project_orchestrator.agent_eval", "validate-suite", "engineering/agent-eval-suite.json"]),
    ("agent_eval_runner_config", [sys.executable, "-m", "project_orchestrator.agent_eval_runner", "validate-config", "engineering/examples/agent-eval-runner.example.json"]),
    ("specialist_lanes", [sys.executable, "engineering/validate_specialist_lanes.py"]),
    ("work_decomposition", [sys.executable, "engineering/validate_work_decomposition.py"]),
    ("regression_prevention", [sys.executable, "-m", "project_orchestrator.cli", "regression-prevention", "--events", "engineering/fixtures/regression-prevention-smoke-events.json", "--policies", "engineering/fixtures/regression-prevention-smoke-policies.json"]),
    ("throughput_optimizer", [sys.executable, "-m", "project_orchestrator.cli", "throughput-optimize"]),
    ("learning_memory", [sys.executable, "engineering/validate_learning_memory.py"]),
    ("learning_candidates", [sys.executable, "-m", "project_orchestrator.learning_candidates", "validate"]),
    ("repository_skills", [sys.executable, "-m", "project_orchestrator.skill_extraction", "validate"]),
    ("transient_memory", [sys.executable, "-m", "project_orchestrator.transient_memory", "self-test"]),
    ("compounding_timeseries", [sys.executable, "engineering/validate_timeseries.py"]),
    ("tooling_capabilities", [sys.executable, "engineering/validate_tooling_capabilities.py"]),
    ("n8n_policy", [sys.executable, "engineering/validate_n8n.py"]),
    ("compile", [sys.executable, "-m", "compileall", "-q", "src", "tests", "engineering"]),
    ("tests", [sys.executable, "-m", "pytest", "-q"]),
    ("cli_validate", [sys.executable, "-m", "project_orchestrator.cli", "validate"]),
    ("reinvestment_plan_smoke", [sys.executable, "-m", "project_orchestrator.cli", "reinvestment-plan"]),
]

def write_evidence(
    *,
    status: str,
    results: list[dict],
    active_check: str | None,
    started_at: str,
) -> None:
    EVIDENCE.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "repository": "jweter/project-orchestrator",
                "head_sha": git_head(ROOT),
                "mode": "FULL",
                "started_at": started_at,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "active_check": active_check,
                "results": results,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def timeout_for_check(name: str) -> int:
    return TEST_CHECK_TIMEOUT_SECONDS if name == "tests" else DEFAULT_CHECK_TIMEOUT_SECONDS


def run_check(name: str, argv: list[str]) -> dict:
    timeout_seconds = timeout_for_check(name)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=PREFLIGHT_ENV,
            timeout=timeout_seconds,
        )
        returncode = completed.returncode
        stdout = completed.stdout[-4000:]
        stderr = completed.stderr[-4000:]
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = (exc.stdout or "")
        stderr = (exc.stderr or "")
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        stdout = stdout[-4000:]
        stderr = (
            stderr[-3500:]
            + f"\nPREFLIGHT_CHECK_TIMEOUT:{name}:{timeout_seconds}s"
        )
        timed_out = True
    return {
        "name": name,
        "argv": argv,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    results: list[dict] = []
    started_at = datetime.now(timezone.utc).isoformat()
    write_evidence(
        status="RUNNING",
        results=results,
        active_check=None,
        started_at=started_at,
    )

    ok = True
    for name, argv in checks:
        write_evidence(
            status="RUNNING",
            results=results,
            active_check=name,
            started_at=started_at,
        )
        result = run_check(name, argv)
        results.append(result)
        if result["returncode"] != 0:
            ok = False
            break

    write_evidence(
        status="GREEN" if ok else "FAILED",
        results=results,
        active_check=None,
        started_at=started_at,
    )

    for result in results:
        outcome = "PASS" if result["returncode"] == 0 else (
            "TIMEOUT" if result["timed_out"] else "FAIL"
        )
        print(
            f"{result['name']}: {outcome} "
            f"({result['duration_seconds']:.3f}s)"
        )
        if result["returncode"]:
            print(result["stdout"])
            print(result["stderr"], file=sys.stderr)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
