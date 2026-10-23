from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "preflight-evidence.json"
SRC = ROOT / "src"
# Windows Git can fail to create .git/objects in tests when the pytest
# basetemp is nested under a long isolated worktree path. Use a short,
# stable, worktree-specific temp root; never share or delete another repo's
# scratch space. Do not change or skip any tests or verification gates.
_ROOT_KEY = hashlib.sha256(
    os.path.normcase(str(ROOT.resolve())).encode("utf-8")
).hexdigest()[:16]
PREFLIGHT_BASETEMP = Path(tempfile.gettempdir()) / "ig-preflight" / _ROOT_KEY
PREFLIGHT_BASETEMP.parent.mkdir(parents=True, exist_ok=True)
PREFLIGHT_ENV = os.environ.copy()
PREFLIGHT_ENV["PYTHONPATH"] = str(SRC)
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
    # Run the real local controller proof files in a fresh process so their
    # physical-memory safety gate is not biased by the accumulated main suite.
    ("tests", [sys.executable, "-m", "pytest", "-q", "--basetemp", str(PREFLIGHT_BASETEMP),
               "--ignore=tests/test_iron_god_complete_loop_proof.py",
               "--ignore=tests/test_iron_god_orchestration_caller.py",
               "--ignore=tests/test_iron_god_orchestration_caller_product_reality_blocking.py",
               "--ignore=tests/test_iron_god_worker_authorization_boundary.py",
               "--ignore=tests/test_rdi001_resident_dispatch_e2e_proof.py"]),
    ("resident_dispatch_proof", [sys.executable, "-m", "pytest", "-q",
                                  "tests/test_rdi001_resident_dispatch_e2e_proof.py",
                                  "--basetemp", str(PREFLIGHT_BASETEMP / "resident-dispatch")]),
    ("controller_proofs", [sys.executable, "-m", "pytest", "-q",
                            "tests/test_iron_god_complete_loop_proof.py",
                            "tests/test_iron_god_orchestration_caller.py",
                            "tests/test_iron_god_orchestration_caller_product_reality_blocking.py",
                            "tests/test_iron_god_worker_authorization_boundary.py",
                            "--basetemp", str(PREFLIGHT_BASETEMP / "controller-proofs")]),
    ("cli_validate", [sys.executable, "-m", "project_orchestrator.cli", "validate"]),
    ("reinvestment_plan_smoke", [sys.executable, "-m", "project_orchestrator.cli", "reinvestment-plan"]),
]

def write_evidence(
    *,
    status: str,
    results: list[dict],
    active_check: str | None,
    started_at: str,
    head_sha: str | None = None,
) -> None:
    EVIDENCE.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "repository": "jweter/project-orchestrator",
                "head_sha": head_sha or git_head(ROOT),
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


def exact_head_fence_result(expected_head: str, observed_head: str) -> dict:
    return {
        "name": "exact_head_fence",
        "argv": [],
        "returncode": 125,
        "stdout": "",
        "stderr": (
            f"PREFLIGHT_HEAD_MOVED:expected={expected_head}:observed={observed_head}"
        ),
        "timed_out": False,
        "interrupted": False,
        "timeout_seconds": 0,
        "duration_seconds": 0.0,
        "expected_head_sha": expected_head,
        "observed_head_sha": observed_head,
    }


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
        interrupted = False
    except KeyboardInterrupt:
        returncode = 130
        stdout = ""
        stderr = f"PREFLIGHT_CHECK_INTERRUPTED:{name}"
        timed_out = False
        interrupted = True
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
        interrupted = False
    return {
        "name": name,
        "argv": argv,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "interrupted": interrupted,
        "timeout_seconds": timeout_seconds,
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    results: list[dict] = []
    started_at = datetime.now(timezone.utc).isoformat()
    PREFLIGHT_BASETEMP.parent.mkdir(parents=True, exist_ok=True)
    expected_head = git_head(ROOT)
    if not expected_head or expected_head == "UNKNOWN":
        results.append(exact_head_fence_result("KNOWN_HEAD_REQUIRED", expected_head))
        write_evidence(
            status="FAILED",
            results=results,
            active_check=None,
            started_at=started_at,
            head_sha=expected_head,
        )
        return 1

    write_evidence(
        status="RUNNING",
        results=results,
        active_check=None,
        started_at=started_at,
        head_sha=expected_head,
    )

    ok = True
    for name, argv in checks:
        observed_before = git_head(ROOT)
        if observed_before != expected_head:
            results.append(exact_head_fence_result(expected_head, observed_before))
            ok = False
            break

        write_evidence(
            status="RUNNING",
            results=results,
            active_check=name,
            started_at=started_at,
            head_sha=expected_head,
        )
        result = run_check(name, argv)
        results.append(result)

        observed_after = git_head(ROOT)
        if observed_after != expected_head:
            results.append(exact_head_fence_result(expected_head, observed_after))
            ok = False
            break

        if result["returncode"] != 0:
            ok = False
            break

    interrupted = any(result.get("interrupted") for result in results)
    final_status = "INTERRUPTED" if interrupted else ("GREEN" if ok else "FAILED")
    write_evidence(
        status=final_status,
        results=results,
        active_check=None,
        started_at=started_at,
        head_sha=expected_head,
    )

    for result in results:
        outcome = "PASS" if result["returncode"] == 0 else (
            "INTERRUPTED" if result.get("interrupted") else (
                "TIMEOUT" if result["timed_out"] else "FAIL"
            )
        )
        print(
            f"{result['name']}: {outcome} "
            f"({result['duration_seconds']:.3f}s)"
        )
        if result["returncode"]:
            print(result["stdout"])
            print(result["stderr"], file=sys.stderr)

    return 130 if interrupted else (0 if ok else 1)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(PREFLIGHT_BASETEMP, ignore_errors=True)