from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
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
# Output is diagnostic and potentially sensitive: keep it under the local
# user's OS temp directory, never inside the repository or committed evidence.
FAILURE_LOG_ROOT = Path(tempfile.gettempdir()) / "ig-preflight-failures" / _ROOT_KEY
MAX_FAILURE_STREAM_CHARS = 2 * 1024 * 1024
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
    # Real-worker controller and dispatch proofs run against disposable
    # repositories with fixture-scoped resources (including explicit veto
    # regressions). Never weaken the live worker's RAM/disk gate or read
    # production authorization as test success; use fresh test processes.
    ("controller_proofs", [sys.executable, "-m", "pytest", "-q",
                            "tests/test_iron_god_complete_loop_proof.py",
                            "tests/test_iron_god_orchestration_caller.py",
                            "tests/test_iron_god_orchestration_caller_product_reality_blocking.py",
                            "tests/test_iron_god_worker_authorization_boundary.py",
                            "--basetemp", str(PREFLIGHT_BASETEMP / "controller-proofs")]),
    ("resident_dispatch_proof", [sys.executable, "-m", "pytest", "-q",
                                  "tests/test_rdi001_resident_dispatch_e2e_proof.py",
                                  "--basetemp", str(PREFLIGHT_BASETEMP / "resident-dispatch")]),
    ("tests", [sys.executable, "-m", "pytest", "-q", "--basetemp", str(PREFLIGHT_BASETEMP / "main"),
               "--ignore=tests/test_iron_god_complete_loop_proof.py",
               "--ignore=tests/test_iron_god_orchestration_caller.py",
               "--ignore=tests/test_iron_god_orchestration_caller_product_reality_blocking.py",
               "--ignore=tests/test_iron_god_worker_authorization_boundary.py",
               "--ignore=tests/test_rdi001_resident_dispatch_e2e_proof.py"]),
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


# Diagnostics are local private observations, never authoritative evidence of
# PASS or a substitute for native exact-head verification.
_SECRET_MARKERS = re.compile(
    r"(?i)\b(?:authorization\s*:\s*bearer\s+|"
    r"(?:password|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*)"
    r"[^\s,;]+|\bgh[pousr]_[A-Za-z0-9_]{20,}|\bsk-[A-Za-z0-9_-]{20,}"
)
_PYTEST_FAILED_NODE = re.compile(r"^FAILED\s+(tests/[^\s]+)", re.MULTILINE)


def _redact_diagnostic(text: str) -> str:
    """Best-effort credential masking before *local-only* persistence."""
    return _SECRET_MARKERS.sub("[REDACTED_SECRET]", text)


def _bounded_diagnostic(text: str) -> tuple[str, int]:
    """Retain both first and last traceback areas when output is excessive."""
    if len(text) <= MAX_FAILURE_STREAM_CHARS:
        return _redact_diagnostic(text), 0
    first = MAX_FAILURE_STREAM_CHARS // 2
    last = MAX_FAILURE_STREAM_CHARS - first
    omitted = len(text) - MAX_FAILURE_STREAM_CHARS
    preview = (
        text[:first] + f"\n[DIAGNOSTIC_TRUNCATED:{omitted}_CHARS]\n" + text[-last:]
    )
    return _redact_diagnostic(preview), omitted


def _write_failure_diagnostic(
    name: str, stdout: str, stderr: str, *,
    returncode: int, timed_out: bool,
) -> dict:
    """Persist failed check output outside Git with bounded size and unique name.

    Never allow an I/O error in optional diagnostics to falsify a failing
    check's nonzero exit. The log is not safe to upload or commit.
    """
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:48] or "check"
    FAILURE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    path = FAILURE_LOG_ROOT / f"{safe_name}-{uuid.uuid4().hex}.log"
    bounded_out, omitted_out = _bounded_diagnostic(stdout)
    bounded_err, omitted_err = _bounded_diagnostic(stderr)
    with path.open("x", encoding="utf-8", newline="\n") as out:
        out.write(
            f"PREFLIGHT_FAILED_CHECK={safe_name}\n"
            f"EXIT_CODE={returncode}\nTIMED_OUT={timed_out}\n"
            f"STDOUT_ORIGINAL_CHARS={len(stdout)}\n"
            f"STDERR_ORIGINAL_CHARS={len(stderr)}\n"
            f"STDOUT_OMITTED_CHARS={omitted_out}\n"
            f"STDERR_OMITTED_CHARS={omitted_err}\n"
            "LOCAL_PRIVATE_DIAGNOSTIC=DO_NOT_COMMIT_OR_PUBLISH\n"
            "\n=== STDOUT ===\n"
        )
        out.write(bounded_out)
        out.write("\n=== STDERR ===\n")
        out.write(bounded_err)
        out.write("\n")
    return {
        "path": str(path),
        "stdout_original_chars": len(stdout),
        "stderr_original_chars": len(stderr),
        "stdout_omitted_chars": omitted_out,
        "stderr_omitted_chars": omitted_err,
        "redaction": "BEST_EFFORT_LOCAL_ONLY",
    }


def _failed_test_nodes(stdout: str) -> list[str]:
    # The JSON projection stays small even when the full pytest failure list
    # is extensive; the local diagnostic is the evidence source for details.
    return list(dict.fromkeys(_PYTEST_FAILED_NODE.findall(stdout)))[:100]


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
        stdout = completed.stdout
        stderr = completed.stderr
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
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        stderr += f"\nPREFLIGHT_CHECK_TIMEOUT:{name}:{timeout_seconds}s"
        timed_out = True
        interrupted = False

    diagnostics: dict = {}
    if returncode != 0:
        try:
            diagnostics = _write_failure_diagnostic(
                name, stdout, stderr,
                returncode=returncode, timed_out=timed_out,
            )
        except OSError as exc:
            # Failing to archive logs cannot manufacture a green verdict.
            diagnostics = {"error": "DIAGNOSTIC_WRITE_FAILED:" + type(exc).__name__}

    return {
        "name": name,
        "argv": argv,
        "returncode": returncode,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
        "timed_out": timed_out,
        "interrupted": interrupted,
        "timeout_seconds": timeout_seconds,
        "duration_seconds": round(time.monotonic() - started, 3),
        "failure_diagnostic": diagnostics or None,
        "failed_test_nodes": _failed_test_nodes(stdout) if name == "tests" and returncode else [],
    }


def main() -> int:
    results: list[dict] = []
    started_at = datetime.now(timezone.utc).isoformat()
    PREFLIGHT_BASETEMP.mkdir(parents=True, exist_ok=True)
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