from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ALLOWED_REPOSITORIES = {
    "jweter/knowledge-engine-core",
    "jweter/knowledge-engine-web",
    "jweter/knowledge-engine-ai",
    "jweter/rocksmith-cdlc-generator",
    "jweter/Project-Everward",
}
KNOWLEDGE_ENGINE_CORE = "jweter/knowledge-engine-core"
KNOWLEDGE_ENGINE_ADAPTER = "knowledge_engine_core"
KNOWLEDGE_ENGINE_CHECKS = {"preflight", "ollama_health"}
KNOWLEDGE_ENGINE_ENVIRONMENT_ID = "exponential-engine-laptop-actor-v1"
MAX_NATIVE_SECONDS = 7200


class ActorEnvironmentError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _normalize_origin(value: str) -> str:
    text = value.strip().replace("\\", "/").rstrip("/")
    if text.startswith("git@github.com:"):
        text = "https://github.com/" + text.removeprefix("git@github.com:")
    return text.lower().removesuffix(".git")


def _expected_origin(repository: str) -> str:
    return f"https://github.com/{repository}".lower()


def _validate_repository(repository: str) -> None:
    if repository not in ALLOWED_REPOSITORIES:
        raise ActorEnvironmentError("REPOSITORY_NOT_ALLOWED")


def _validate_workspace_origin(workspace: Path, repository: str) -> None:
    origin = run(["git", "remote", "get-url", "origin"], workspace)
    if origin.returncode != 0 or _normalize_origin(origin.stdout) != _expected_origin(repository):
        raise ActorEnvironmentError("ORIGIN_MISMATCH")


def _clone_workspace(repository: str, workspace: Path) -> None:
    gh = shutil.which("gh")
    if gh is None:
        raise ActorEnvironmentError("GITHUB_CLI_UNAVAILABLE")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    clone = subprocess.run(
        [gh, "repo", "clone", repository, str(workspace), "--", "--filter=blob:none"],
        cwd=workspace.parent,
        text=True,
        capture_output=True,
        check=False,
    )
    if clone.returncode != 0 or not (workspace / ".git").is_dir():
        raise ActorEnvironmentError("WORKSPACE_CLONE_FAILED")


def ensure_managed_workspace(job: dict[str, Any], workspace_root: Path) -> Path:
    repository = str(job["repository"])
    _validate_repository(repository)
    repo_name = repository.split("/", 1)[1]
    workspace = workspace_root / repo_name
    if workspace.exists() and not (workspace / ".git").is_dir():
        raise ActorEnvironmentError("WORKSPACE_NOT_GIT")
    if not workspace.exists():
        _clone_workspace(repository, workspace)
    _validate_workspace_origin(workspace, repository)
    return workspace


def _prepare_exact_base(job: dict[str, Any], workspace: Path) -> str:
    dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], workspace)
    if dirty.returncode != 0:
        raise ActorEnvironmentError("WORKTREE_STATUS_FAILED")
    if dirty.stdout.strip():
        raise ActorEnvironmentError("TRACKED_WORKTREE_DIRTY")

    # Best-effort refresh. A transient network problem does not invalidate an exact
    # commit already present in this disposable actor-owned checkout.
    run(["git", "fetch", "--prune", "origin"], workspace)

    base_sha = str(job["base_sha"])
    base = run(["git", "cat-file", "-e", f"{base_sha}^{{commit}}"], workspace)
    if base.returncode != 0:
        raise ActorEnvironmentError("EXACT_BASE_UNAVAILABLE")

    checkout = run(["git", "checkout", "-B", str(job["branch"]), base_sha], workspace)
    if checkout.returncode != 0:
        raise ActorEnvironmentError("EXACT_BASE_CHECKOUT_FAILED")

    head = run(["git", "rev-parse", "HEAD"], workspace)
    if head.returncode != 0 or head.stdout.strip() != base_sha:
        raise ActorEnvironmentError("EXACT_BASE_IDENTITY_MISMATCH")
    return base_sha


def _changed_paths(workspace: Path, base_sha: str) -> list[str]:
    changed = run(["git", "diff", "--name-only", base_sha, "HEAD"], workspace)
    if changed.returncode != 0:
        raise ActorEnvironmentError("CHANGED_PATHS_UNAVAILABLE")
    return sorted(filter(None, changed.stdout.splitlines()))


def _run_generic_verification(
    job: dict[str, Any], workspace: Path
) -> tuple[str, list[dict[str, Any]], str | None]:
    verification: list[dict[str, Any]] = []
    passed = True
    commands = job.get("verification", [])
    if not isinstance(commands, list) or not commands:
        raise ActorEnvironmentError("VERIFICATION_PLAN_MISSING")
    for command in commands:
        if not isinstance(command, str) or not command.strip():
            raise ActorEnvironmentError("VERIFICATION_PLAN_INVALID")
        proc = subprocess.run(
            command,
            cwd=workspace,
            shell=True,
            text=True,
            capture_output=True,
            check=False,
        )
        verification.append({"command": command, "returncode": proc.returncode})
        passed = passed and proc.returncode == 0
    return ("PASS" if passed else "FAIL"), verification, (None if passed else "TEST_FAILURE")


def _validate_ke_checks(job: dict[str, Any]) -> list[str]:
    checks = job.get("requested_checks")
    if not isinstance(checks, list) or not checks:
        raise ActorEnvironmentError("NATIVE_CHECKS_MISSING")
    if any(not isinstance(item, str) or not item for item in checks):
        raise ActorEnvironmentError("NATIVE_CHECKS_INVALID")
    if len(set(checks)) != len(checks):
        raise ActorEnvironmentError("NATIVE_CHECKS_DUPLICATE")
    if not set(checks).issubset(KNOWLEDGE_ENGINE_CHECKS):
        raise ActorEnvironmentError("NATIVE_CHECK_NOT_AUTHORIZED")
    return checks


def _ensure_ke_environment(workspace: Path) -> str:
    poetry = shutil.which("poetry")
    if poetry is None:
        raise ActorEnvironmentError("POETRY_UNAVAILABLE")
    install = subprocess.run(
        [poetry, "install", "--no-interaction", "--no-ansi"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=MAX_NATIVE_SECONDS,
    )
    if install.returncode != 0:
        raise ActorEnvironmentError("DEPENDENCY_BOOTSTRAP_FAILED")
    return poetry


def _run_ke_native_worker(
    job: dict[str, Any],
    workspace: Path,
    outbox: Path,
) -> tuple[str, list[dict[str, Any]], str | None]:
    if job["repository"] != KNOWLEDGE_ENGINE_CORE:
        raise ActorEnvironmentError("NATIVE_ADAPTER_REPOSITORY_MISMATCH")
    checks = _validate_ke_checks(job)
    poetry = _ensure_ke_environment(workspace)

    state_dir = outbox.parent / "state" / str(job["job_id"])
    state_dir.mkdir(parents=True, exist_ok=True)
    request_path = state_dir / "request.json"
    native_result_path = state_dir / "native-result.json"
    request = {
        "repository": job["repository"],
        "branch": job["branch"],
        "exact_sha": job["base_sha"],
        "request_id": job["job_id"],
        "requested_checks": checks,
        "environment_id": KNOWLEDGE_ENGINE_ENVIRONMENT_ID,
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    atomic_json(request_path, request)

    proc = subprocess.run(
        [
            poetry,
            "run",
            "python",
            "-m",
            "knowledge_engine.unattended_worker",
            "--request",
            str(request_path),
            "--environment-id",
            KNOWLEDGE_ENGINE_ENVIRONMENT_ID,
            "--repo-root",
            str(workspace),
            "--state-dir",
            str(state_dir),
            "--result",
            str(native_result_path),
        ],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=MAX_NATIVE_SECONDS,
    )
    if not native_result_path.is_file():
        raise ActorEnvironmentError("NATIVE_WORKER_RESULT_MISSING")
    try:
        native = json.loads(native_result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActorEnvironmentError("NATIVE_WORKER_RESULT_INVALID") from exc

    identity = {
        "request_id": job["job_id"],
        "repository": job["repository"],
        "exact_sha": job["base_sha"],
        "environment_id": KNOWLEDGE_ENGINE_ENVIRONMENT_ID,
    }
    if any(native.get(key) != value for key, value in identity.items()):
        raise ActorEnvironmentError("NATIVE_WORKER_IDENTITY_MISMATCH")
    status = native.get("status")
    allowed_statuses = {
        "PASS",
        "FAIL",
        "REVIEW_REQUIRED",
        "PRODUCT_REALITY_REQUIRED",
        "ENVIRONMENT_FAILURE",
    }
    if status not in allowed_statuses:
        raise ActorEnvironmentError("NATIVE_WORKER_STATUS_INVALID")
    failure_class = native.get("failure_class")
    safe_failure_class = str(failure_class)[:100] if failure_class else None
    verification = [
        {
            "adapter": KNOWLEDGE_ENGINE_ADAPTER,
            "checks": checks,
            "status": status,
            "failure_class": safe_failure_class,
            "process_returncode": proc.returncode,
        }
    ]
    return str(status), verification, safe_failure_class


def _claim_job(job_path: Path) -> Path:
    claimed = job_path.with_suffix(job_path.suffix + ".running")
    try:
        job_path.replace(claimed)
    except FileNotFoundError as exc:
        raise ActorEnvironmentError("JOB_CLAIM_RACE") from exc
    return claimed


def recover_interrupted_jobs(inbox: Path) -> int:
    recovered = 0
    for claimed in sorted(inbox.glob("*.job.json.running")):
        original = Path(str(claimed).removesuffix(".running"))
        if original.exists():
            raise ActorEnvironmentError("INTERRUPTED_JOB_COLLISION")
        claimed.replace(original)
        recovered += 1
    return recovered


def execute(job_path: Path, workspace_root: Path, outbox: Path) -> None:
    claimed_path = _claim_job(job_path)
    job = json.loads(claimed_path.read_text(encoding="utf-8"))
    result: dict[str, Any] = {
        "job_id": job["job_id"],
        "packet_id": job["packet_id"],
        "repository": job["repository"],
        "branch": job["branch"],
        "base_sha": job["base_sha"],
        "changed_paths": [],
        "verification": [],
        "evidence_ref": f"local://laptop-actor/{job['job_id']}",
        "failure_class": None,
    }
    try:
        workspace = ensure_managed_workspace(job, workspace_root)
        base_sha = _prepare_exact_base(job, workspace)
        adapter = job.get("execution_adapter")
        if adapter is None:
            status, verification, failure_class = _run_generic_verification(job, workspace)
        elif adapter == KNOWLEDGE_ENGINE_ADAPTER:
            status, verification, failure_class = _run_ke_native_worker(job, workspace, outbox)
        else:
            raise ActorEnvironmentError("EXECUTION_ADAPTER_NOT_AUTHORIZED")

        head = run(["git", "rev-parse", "HEAD"], workspace)
        if head.returncode != 0:
            raise ActorEnvironmentError("EXACT_HEAD_UNAVAILABLE")
        result["status"] = status
        result["verification"] = verification
        result["failure_class"] = failure_class
        result["exact_head_sha"] = head.stdout.strip()
        result["changed_paths"] = _changed_paths(workspace, base_sha)
    except subprocess.TimeoutExpired:
        result["status"] = "ENVIRONMENT_FAILURE"
        result["exact_head_sha"] = str(job.get("base_sha", ""))
        result["failure_class"] = "WORKER_TIMEOUT"
        result["verification"] = [{"runner_error_code": "WORKER_TIMEOUT"}]
    except ActorEnvironmentError as exc:
        result["status"] = "ENVIRONMENT_FAILURE"
        result["exact_head_sha"] = str(job.get("base_sha", ""))
        result["failure_class"] = exc.code
        result["verification"] = [{"runner_error_code": exc.code}]
    except Exception:
        result["status"] = "ENVIRONMENT_FAILURE"
        result["exact_head_sha"] = str(job.get("base_sha", ""))
        result["failure_class"] = "UNEXPECTED_RUNNER_FAILURE"
        result["verification"] = [{"runner_error_code": "UNEXPECTED_RUNNER_FAILURE"}]
    finally:
        atomic_json(outbox / f"{job['job_id']}.result.json", result)
        done = Path(str(claimed_path).removesuffix(".running") + ".done")
        claimed_path.replace(done)


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent bounded Windows laptop actor")
    parser.add_argument("--queue", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    queue = Path(args.queue)
    inbox, outbox = queue / "inbox", queue / "outbox"
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    recover_interrupted_jobs(inbox)
    while True:
        jobs = sorted(inbox.glob("*.job.json"))
        if jobs:
            execute(jobs[0], Path(args.workspace_root), outbox)
        if args.once:
            return 0
        time.sleep(max(10, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
