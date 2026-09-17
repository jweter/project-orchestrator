from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)


def atomic_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def execute(job_path: Path, workspace_root: Path, outbox: Path) -> None:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    repo = job["repository"].split("/", 1)[1]
    workspace = workspace_root / repo
    result = {
        "job_id": job["job_id"], "packet_id": job["packet_id"], "repository": job["repository"],
        "branch": job["branch"], "base_sha": job["base_sha"], "changed_paths": [], "verification": [],
        "evidence_ref": f"local://laptop-actor/{job['job_id']}",
    }
    try:
        if not workspace.is_dir():
            raise RuntimeError("workspace missing")
        if run(["git", "status", "--porcelain"], workspace).stdout.strip():
            raise RuntimeError("worktree is not clean")
        run(["git", "fetch", "origin"], workspace)
        base = run(["git", "rev-parse", job["base_sha"]], workspace)
        if base.returncode != 0 or base.stdout.strip() != job["base_sha"]:
            raise RuntimeError("exact base SHA unavailable")
        checkout = run(["git", "checkout", "-B", job["branch"], job["base_sha"]], workspace)
        if checkout.returncode != 0:
            raise RuntimeError(checkout.stderr.strip() or "checkout failed")

        verification = []
        passed = True
        for command in job.get("verification", []):
            proc = subprocess.run(command, cwd=workspace, shell=True, text=True, capture_output=True, check=False)
            verification.append({"command": command, "returncode": proc.returncode})
            passed = passed and proc.returncode == 0
        result["verification"] = verification
        result["status"] = "PASS" if passed else "FAIL"
        result["exact_head_sha"] = run(["git", "rev-parse", "HEAD"], workspace).stdout.strip()
        changed = run(["git", "diff", "--name-only", job["base_sha"], "HEAD"], workspace)
        result["changed_paths"] = sorted(filter(None, changed.stdout.splitlines()))
    except Exception as exc:
        result["status"] = "ENVIRONMENT_FAILURE"
        result["exact_head_sha"] = job["base_sha"]
        result["verification"] = [{"runner_error": str(exc)}]
    finally:
        atomic_json(outbox / f"{job['job_id']}.result.json", result)
        job_path.rename(job_path.with_suffix(job_path.suffix + ".done"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent bounded Windows laptop actor")
    parser.add_argument("--queue", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    queue = Path(args.queue)
    inbox, outbox = queue / "inbox", queue / "outbox"
    inbox.mkdir(parents=True, exist_ok=True); outbox.mkdir(parents=True, exist_ok=True)
    while True:
        jobs = sorted(inbox.glob("*.job.json"))
        if jobs:
            execute(jobs[0], Path(args.workspace_root), outbox)
        if args.once:
            return 0
        time.sleep(max(10, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
