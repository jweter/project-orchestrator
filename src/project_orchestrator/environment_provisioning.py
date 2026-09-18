from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from typing import Any


def detect_environment(repo_root: Path) -> dict[str, Any]:
    markers = {
        "poetry": repo_root / "pyproject.toml",
        "npm": repo_root / "package-lock.json",
        "pnpm": repo_root / "pnpm-lock.yaml",
        "yarn": repo_root / "yarn.lock",
    }
    present = [name for name, path in markers.items() if path.exists()]
    return {
        "schema_version": 1,
        "repo_root": str(repo_root),
        "package_managers": present,
        "python_available": shutil.which("python") is not None,
        "poetry_available": shutil.which("poetry") is not None,
        "npm_available": shutil.which("npm") is not None,
        "pnpm_available": shutil.which("pnpm") is not None,
        "yarn_available": shutil.which("yarn") is not None,
    }


def provisioning_plan(repo_root: Path) -> dict[str, Any]:
    evidence = detect_environment(repo_root)
    managers = evidence["package_managers"]
    commands: list[list[str]] = []
    blockers: list[str] = []
    if "poetry" in managers:
        if evidence["poetry_available"]:
            commands.append(["poetry", "install", "--no-interaction"])
        else:
            blockers.append("poetry_manifest_present_but_poetry_unavailable")
    if "npm" in managers:
        if evidence["npm_available"]:
            commands.append(["npm", "ci"])
        else:
            blockers.append("package_lock_present_but_npm_unavailable")
    if "pnpm" in managers:
        if evidence["pnpm_available"]:
            commands.append(["pnpm", "install", "--frozen-lockfile"])
        else:
            blockers.append("pnpm_lock_present_but_pnpm_unavailable")
    if "yarn" in managers:
        if evidence["yarn_available"]:
            commands.append(["yarn", "install", "--immutable"])
        else:
            blockers.append("yarn_lock_present_but_yarn_unavailable")
    return {
        **evidence,
        "commands": commands,
        "blockers": blockers,
        "status": "READY" if not blockers else "ENVIRONMENT_FAILURE",
        "authority": "dependency provisioning only; never changes repository manifests or lock files",
    }


def provision_environment(repo_root: Path, *, timeout_seconds: int = 900) -> dict[str, Any]:
    plan = provisioning_plan(repo_root)
    if plan["status"] != "READY":
        return {**plan, "executed": [], "status": "ENVIRONMENT_FAILURE"}
    executed: list[dict[str, Any]] = []
    for argv in plan["commands"]:
        completed = subprocess.run(
            argv,
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        record = {
            "argv": argv,
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        }
        executed.append(record)
        if completed.returncode != 0:
            return {**plan, "executed": executed, "status": "ENVIRONMENT_FAILURE"}
    return {**plan, "executed": executed, "status": "PROVISIONED"}
