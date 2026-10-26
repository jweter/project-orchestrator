"""Single bounded Canon worker for one Iron God commissioning packet.

The supervisor's commissioning lane launches this process for exactly one
packet in an isolated Git worktree. It re-checks the authoritative execution
decision before starting and continuously while the coding agent runs (the
agent runs inside a kill-on-close Job Object, so revocation stops its whole
process tree). It commits only allowed paths, runs the packet's verification
commands, and writes a Canon handback. It never pushes, merges, publishes or
touches owner control. A worker PASS is not acceptance: the supervisor
independently re-verifies the exact handed-back head.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT / "src"), str(ROOT)):
    if _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

from project_orchestrator.execution_authority import evaluate_execution_authority  # noqa: E402
from project_orchestrator.execution_runtime import NativeWindowsRuntime, RuntimeRequest  # noqa: E402

MAX_TAIL = 4000
LEAN_AIDER_FLAGS = ("--map-tokens", "0", "--no-detect-urls", "--edit-format", "whole")


def lean_aider_invocation(base: Callable[..., tuple[list[str], dict[str, str]]], message: str,
                          files: list[str]) -> tuple[list[str], dict[str, str]]:
    """Measured on Azathoth (capability ladder): lean context and whole-file edits build the
    commissioning slice in ~60-80 s; a repository map or extra chat files never finish."""
    argv, env = base(message)
    return [*argv, *LEAN_AIDER_FLAGS, *files], env


def private_temp_env(env: dict[str, str], root: Path) -> dict[str, str]:
    """pytest's shared %TEMP%/pytest-of-<user> link can be access-denied; isolate it per run."""
    root.mkdir(parents=True, exist_ok=True)
    return {**env, "TEMP": str(root), "TMP": str(root)}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False, timeout=120)


def _tail(text: str) -> str:
    return text[-MAX_TAIL:] if text else ""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def _under(path: str, roots: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(normalized == root.rstrip("/") or normalized.startswith(root.rstrip("/") + "/") for root in roots)


def changed_paths(worktree: Path) -> list[str]:
    status = _git(worktree, "status", "--porcelain", "--untracked-files=all")
    paths = []
    for line in status.stdout.splitlines():
        entry = line[3:].strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        paths.append(entry.strip('"').replace("\\", "/"))
    return sorted(set(paths))


def worker_message(packet: dict[str, Any]) -> str:
    plan = packet["planning"]
    lines = [
        "You are the Iron God Canon worker for ONE bounded local slice in an isolated Git worktree.",
        f"Objective: {packet['task']}",
        "Acceptance criteria:",
        *[f"- {item}" for item in plan["acceptance_criteria"]],
        "Create or edit ONLY these files: " + ", ".join(packet["paths"]),
        "Do not create or edit any other file. Do not run git, push or publish.",
        "Keep the change minimal, readable and covered by the requested tests.",
    ]
    # Name no other file and send no memory text: aider adds every file the
    # message names to the chat, and on this CPU-only laptop a 16-22k token
    # prompt never completes. Forbidden paths are enforced after the run by
    # the scope check; memory context stays on the packet for the supervisor.
    return "\n".join(lines)


def run_verification(worktree: Path, commands: list[str], *, timeout: int = 900) -> list[dict[str, Any]]:
    results = []
    env = private_temp_env({**os.environ, "PYTHONPATH": str(worktree / "src")},
                           worktree.parent / f"{worktree.name}.tmp")
    for command in commands:
        argv = shlex.split(command, posix=False)
        if argv and argv[0] in {"python", "python.exe", "py"}:
            argv = [sys.executable, *argv[1:]]
        try:
            proc = subprocess.run(argv, cwd=worktree, env=env, capture_output=True, text=True,
                                  check=False, timeout=timeout)
            results.append({"command": command, "returncode": proc.returncode, "ok": proc.returncode == 0,
                            "output_tail": _tail(proc.stdout + "\n" + proc.stderr)})
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append({"command": command, "returncode": None, "ok": False,
                            "output_tail": f"{type(exc).__name__}: {exc}"})
    return results


def execute_packet(
    packet: dict[str, Any],
    *,
    authority: Callable[[], bool],
    run_agent: Callable[[Path, str, list[str], Callable[[], None]], Any],
) -> dict[str, Any]:
    """Implement one packet; returns the Canon handback (never raises for work failures)."""
    worktree = Path(packet["worktree"])
    base = packet["base_sha"]
    handback: dict[str, Any] = {
        "schema_version": 1,
        "packet_id": packet["packet_id"],
        "project": packet["project"],
        "repository": packet["repository"],
        "branch": packet["branch"],
        "head_sha": base,
        "probe_id": packet["packet_id"],
        "pattern_id": "iron-god-canon-commissioning",
        "evidence_ref": f"local://iron-god-canon/{packet['packet_id']}/handback",
        "observed_at": _now(),
        "status": "ENVIRONMENT_FAILURE",
        "summary": "",
        "changed_paths": [],
        "verification": [],
    }

    def finish(status: str, summary: str) -> dict[str, Any]:
        head = _git(worktree, "rev-parse", "HEAD")
        handback.update(status=status, summary=summary, observed_at=_now(),
                        head_sha=head.stdout.strip() if head.returncode == 0 else base)
        return handback

    if not authority():
        return finish("ENVIRONMENT_FAILURE", "EXECUTION_AUTHORITY_DENIED_BEFORE_START")
    head = _git(worktree, "rev-parse", "HEAD")
    branch = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != base:
        return finish("ENVIRONMENT_FAILURE", "WORKTREE_NOT_AT_EXACT_BASE")
    if branch.stdout.strip() != packet["branch"] or changed_paths(worktree):
        return finish("ENVIRONMENT_FAILURE", "WORKTREE_IDENTITY_OR_CLEANLINESS_UNVERIFIED")

    def check() -> None:
        if not authority():
            raise RuntimeError("EXECUTION_AUTHORITY_REVOKED")

    try:
        run_agent(worktree, worker_message(packet), list(packet["paths"]), check)
    except RuntimeError as exc:
        if "EXECUTION_AUTHORITY_REVOKED" in str(exc):
            return finish("ENVIRONMENT_FAILURE", "EXECUTION_AUTHORITY_REVOKED_DURING_IMPLEMENTATION")
        return finish("ENVIRONMENT_FAILURE", f"AGENT_FAILED: {str(exc)[:300]}")
    except Exception as exc:  # The handback must always be written.
        return finish("ENVIRONMENT_FAILURE", f"AGENT_FAILED: {type(exc).__name__}")

    for artifact in worktree.glob(".aider*"):
        if artifact.is_file():
            artifact.unlink()
        else:
            import shutil

            shutil.rmtree(artifact, ignore_errors=True)
    changed = changed_paths(worktree)
    handback["changed_paths"] = changed
    forbidden = packet["planning"]["forbidden_paths"]
    outside = [p for p in changed if not _under(p, packet["paths"]) or _under(p, forbidden)]
    if outside:
        _git(worktree, "checkout", "--", ".")
        _git(worktree, "clean", "-fd")
        return finish("REVIEW_REQUIRED", "CHANGES_OUTSIDE_ALLOWED_PATHS: " + ", ".join(outside[:10]))
    if not changed:
        return finish("FAIL", "NO_CHANGES_PRODUCED")
    if not authority():
        return finish("ENVIRONMENT_FAILURE", "EXECUTION_AUTHORITY_REVOKED_BEFORE_COMMIT")
    _git(worktree, "add", "--", *changed)
    commit = _git(
        worktree, "-c", "user.name=Iron God Canon Worker", "-c", "user.email=iron-god-canon@localhost",
        "commit", "-q", "-m", f"iron-god canon {packet['packet_id']}: {packet['task'][:60]}",
    )
    if commit.returncode != 0:
        return finish("ENVIRONMENT_FAILURE", "COMMIT_FAILED: " + _tail(commit.stderr)[:300])
    results = run_verification(worktree, list(packet["verification"]))
    handback["verification"] = results
    passed = bool(results) and all(item["ok"] for item in results)
    return finish("PASS" if passed else "FAIL",
                  "worker verification passed" if passed else "worker verification failed")


def production_agent(worktree: Path, message: str, files: list[str], check: Callable[[], None]) -> None:
    from scripts.autonomous_slice_builder import (
        MAX_AIDER_SECONDS,
        _acquire_ollama_lock,
        aider_invocation,
    )
    import shutil

    argv, env = lean_aider_invocation(aider_invocation, message, files)
    lock = _acquire_ollama_lock()
    try:
        result = NativeWindowsRuntime().execute_supervised(
            RuntimeRequest(tuple(argv), worktree, env, MAX_AIDER_SECONDS), check, interval=2.0,
        )
    finally:
        shutil.rmtree(lock, ignore_errors=True)
    if result.returncode != 0:
        raise RuntimeError(f"AIDER_EXIT_{result.returncode}: {_tail(result.stderr)[-300:]}")  # keep the exception line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Iron God Canon single-packet worker")
    parser.add_argument("--packet", required=True)
    parser.add_argument("--handback", required=True)
    args = parser.parse_args(argv)
    packet = json.loads(Path(args.packet).read_text(encoding="utf-8"))
    release = os.environ.get("IRON_GOD_ACCEPTED_SHA")

    def authority() -> bool:
        try:
            return evaluate_execution_authority(release_sha=release).permitted is True
        except Exception:
            return False

    handback = execute_packet(packet, authority=authority, run_agent=production_agent)
    _write(Path(args.handback), handback)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
