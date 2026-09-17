from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

QUEUE_REPOSITORY = "jweter/project-orchestrator"
QUEUE_BRANCH = "laptop-actor-queue"
PENDING_DIR = "engineering/laptop-queue/pending"
RESULTS_DIR = "engineering/laptop-queue/results"
ALLOWED_REPOSITORIES = {
    "jweter/project-orchestrator",
    "jweter/knowledge-engine-core",
    "jweter/knowledge-engine-web",
    "jweter/knowledge-engine-ai",
    "jweter/rocksmith-cdlc-generator",
    "jweter/Project-Everward",
}
ALLOWED_EXECUTION_ADAPTERS = {"knowledge_engine_core"}
KNOWLEDGE_ENGINE_CORE = "jweter/knowledge-engine-core"
KNOWLEDGE_ENGINE_CHECKS = {"preflight", "ollama_health"}
EXPORT_RESULT_FIELDS = {
    "job_id",
    "packet_id",
    "repository",
    "branch",
    "base_sha",
    "exact_head_sha",
    "status",
    "changed_paths",
    "verification",
    "evidence_ref",
    "failure_class",
}


def run_gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], text=True, capture_output=True, check=False)


def github_available() -> bool:
    if shutil.which("gh") is None:
        return False
    return run_gh(["auth", "status"]).returncode == 0


def _api_json(path: str) -> Any:
    proc = run_gh(["api", "--method", "GET", path, "-f", f"ref={QUEUE_BRANCH}"])
    if proc.returncode != 0:
        if "404" in proc.stderr or "Not Found" in proc.stderr:
            return None
        raise RuntimeError("GitHub queue read failed")
    return json.loads(proc.stdout)


def _put_json(path: str, payload: dict[str, Any], message: str) -> None:
    content = base64.b64encode((json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()).decode()
    proc = run_gh(["api", "--method", "PUT", f"repos/{QUEUE_REPOSITORY}/contents/{path}", "-f", f"message={message}", "-f", f"content={content}", "-f", f"branch={QUEUE_BRANCH}"])
    if proc.returncode != 0:
        raise RuntimeError("GitHub queue write failed")


def _delete(path: str, sha: str, message: str) -> None:
    proc = run_gh(["api", "--method", "DELETE", f"repos/{QUEUE_REPOSITORY}/contents/{path}", "-f", f"message={message}", "-f", f"sha={sha}", "-f", f"branch={QUEUE_BRANCH}"])
    if proc.returncode != 0:
        raise RuntimeError("GitHub queue delete failed")


def normalize_remote_job(job: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy queue packets without weakening validation.

    Early durable packets used packet_id as their stable identifier and `.json`
    filenames. The bridge originally required a separate job_id and `.job.json`,
    making those packets permanently invisible. Preserve packet_id and derive the
    missing job_id deterministically so old and new packets share one execution path.
    """
    normalized = dict(job)
    if "job_id" not in normalized and isinstance(normalized.get("packet_id"), str):
        normalized["job_id"] = normalized["packet_id"]
    return normalized


def validate_remote_job(job: dict[str, Any]) -> dict[str, Any]:
    job = normalize_remote_job(job)
    required = {"job_id", "packet_id", "repository", "branch", "base_sha"}
    missing = sorted(required - job.keys())
    if missing:
        raise ValueError(f"remote job missing fields: {missing}")
    if job["repository"] not in ALLOWED_REPOSITORIES:
        raise ValueError("remote job repository is outside authorized portfolio/control-plane scope")
    if job["repository"] == QUEUE_REPOSITORY and not str(job["branch"]).startswith(("orchestrator/", "repair/")):
        raise ValueError("control-plane laptop verification requires a governed feature/repair branch")
    sha = job["base_sha"]
    if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("remote job base_sha is invalid")
    if job.get("merge_authority") is True or job.get("product_reality_authority") is True:
        raise ValueError("remote job exceeds laptop authority")

    adapter = job.get("execution_adapter")
    commands = job.get("verification", [])
    if commands is None:
        commands = []
    if not isinstance(commands, list) or not all(isinstance(item, str) and item for item in commands):
        raise ValueError("remote job verification must contain command strings")
    if adapter is None:
        if not commands:
            raise ValueError("remote job has no verification plan")
    else:
        if adapter not in ALLOWED_EXECUTION_ADAPTERS:
            raise ValueError("remote job execution adapter is not authorized")
        if adapter == "knowledge_engine_core":
            if job["repository"] != KNOWLEDGE_ENGINE_CORE:
                raise ValueError("Knowledge Engine adapter is bound to Core")
            checks = job.get("requested_checks")
            if not isinstance(checks, list) or not checks:
                raise ValueError("Knowledge Engine adapter requires requested_checks")
            if any(not isinstance(item, str) or not item for item in checks):
                raise ValueError("Knowledge Engine requested_checks are invalid")
            if len(set(checks)) != len(checks):
                raise ValueError("Knowledge Engine requested_checks contain duplicates")
            if not set(checks).issubset(KNOWLEDGE_ENGINE_CHECKS):
                raise ValueError("Knowledge Engine requested_check is not authorized")
    return job


def sanitize_result(result: dict[str, Any]) -> dict[str, Any]:
    public = {key: result[key] for key in EXPORT_RESULT_FIELDS if key in result}
    verification: list[dict[str, Any]] = []
    for item in public.get("verification", []):
        if not isinstance(item, dict):
            continue
        if "command" in item:
            verification.append({"command": str(item["command"])[:500], "returncode": item.get("returncode")})
        elif "runner_error_code" in item:
            verification.append({"runner_error_code": str(item["runner_error_code"])[:100]})
        elif "runner_error" in item:
            verification.append({"runner_error": "local runner environment failure"})
        elif item.get("adapter") == "knowledge_engine_core":
            checks = item.get("checks", [])
            safe_checks = [str(value)[:100] for value in checks if isinstance(value, str) and value in KNOWLEDGE_ENGINE_CHECKS]
            verification.append({"adapter": "knowledge_engine_core", "checks": safe_checks, "status": str(item.get("status", ""))[:100], "failure_class": (str(item["failure_class"])[:100] if item.get("failure_class") else None), "process_returncode": item.get("process_returncode")})
    public["verification"] = verification
    if public.get("failure_class") is not None:
        public["failure_class"] = str(public["failure_class"])[:100]
    public["private_payload_exported"] = False
    public["merge_authority"] = False
    public["product_reality_authority"] = False
    return public


def pull_remote_jobs(inbox: Path) -> dict[str, tuple[str, str]]:
    listing = _api_json(f"repos/{QUEUE_REPOSITORY}/contents/{PENDING_DIR}")
    if not listing:
        return {}
    pending: dict[str, tuple[str, str]] = {}
    for entry in listing:
        name = str(entry.get("name", "")) if isinstance(entry, dict) else ""
        if not name.endswith(".json"):
            continue
        detail = _api_json(f"repos/{QUEUE_REPOSITORY}/contents/{entry['path']}")
        if not isinstance(detail, dict):
            continue
        raw = base64.b64decode(str(detail["content"]).replace("\n", "")).decode("utf-8")
        job = validate_remote_job(json.loads(raw))
        local = inbox / f"{job['job_id']}.job.json"
        if not local.exists() and not local.with_suffix(local.suffix + ".done").exists():
            local.write_text(json.dumps(job, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pending[job["job_id"]] = (str(detail["sha"]), str(entry["path"]))
    return pending


def push_results(outbox: Path, pending: dict[str, tuple[str, str]]) -> int:
    published = 0
    for path in sorted(outbox.glob("*.result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        job_id = result.get("job_id")
        if not isinstance(job_id, str) or job_id not in pending:
            continue
        pending_sha, pending_path = pending[job_id]
        remote_result = f"{RESULTS_DIR}/{job_id}.result.json"
        if _api_json(f"repos/{QUEUE_REPOSITORY}/contents/{remote_result}") is None:
            _put_json(remote_result, sanitize_result(result), f"Record laptop actor result {job_id}")
        _delete(pending_path, pending_sha, f"Complete laptop actor job {job_id}")
        path.rename(path.with_suffix(path.suffix + ".published"))
        published += 1
    return published


def run_local_once(runner: Path, queue: Path, workspace_root: Path) -> int:
    proc = subprocess.run([sys.executable, str(runner), "--queue", str(queue), "--workspace-root", str(workspace_root), "--once"], text=True, capture_output=True, check=False)
    return proc.returncode


def cycle(queue: Path, workspace_root: Path, runner: Path) -> dict[str, Any]:
    inbox, outbox = queue / "inbox", queue / "outbox"
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    remote = github_available()
    pending = pull_remote_jobs(inbox) if remote else {}
    local_returncode = run_local_once(runner, queue, workspace_root)
    published = push_results(outbox, pending) if remote else 0
    return {"github_bridge": remote, "pending_remote_jobs": len(pending), "published_results": published, "local_returncode": local_returncode}


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub-backed persistent laptop actor bridge")
    parser.add_argument("--queue", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--runner", required=True)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        try:
            cycle(Path(args.queue), Path(args.workspace_root), Path(args.runner))
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            pass
        if args.once:
            return 0
        time.sleep(max(30, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
