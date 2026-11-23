"""Fixed, local-only read-only command/status surface for the Iron God MCP adapter.

The caller chooses only a fixed operation name. Configured roots and the release
identity come from the local MCP host, never the tool call. This is NOT a
generic shell, remote transport, authorization decision, lease, or dispatcher.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import iron_god_owner_control as owner_control
from . import mcp_coordination as coordination

_COMMANDS = frozenset({"engine_status", "owner_status", "repository_head"})
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _git_read(repo_root: Path, *args: str, changed_exit: bool = False) -> str:
    """Run one fixed, bounded Git read; no shell, prompts, hooks or index locks."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    result = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
         "-C", str(repo_root), *args],
        capture_output=True, text=True, check=False, timeout=8,
        shell=False, env=env,
    )
    if changed_exit and result.returncode == 1:
        return "CHANGED"
    if result.returncode != 0:
        raise ValueError("GIT_READ_UNAVAILABLE")
    if len(result.stdout) > 4096:
        raise ValueError("GIT_OUTPUT_LIMIT")
    return result.stdout.strip()


def _repository_head(repo_root: Path) -> dict[str, Any]:
    """Read only the pinned Git root; do not follow a parent checkout."""
    try:
        actual = Path(_git_read(repo_root, "rev-parse", "--show-toplevel")).resolve()
        if actual != repo_root.resolve():
            raise ValueError("REPOSITORY_ROOT_MISMATCH")
        sha = _git_read(repo_root, "rev-parse", "--verify", "HEAD^{commit}")
        if not _SHA.fullmatch(sha):
            raise ValueError("HEAD_INVALID")
        dirty = _git_read(repo_root, "diff", "--quiet", "--no-ext-diff",
                          "--no-textconv", "--ignore-submodules=all", "HEAD", "--",
                          changed_exit=True) == "CHANGED"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"status": "UNAVAILABLE", "reason": "GIT_READ_UNAVAILABLE"}
    return {
        "status": "OK",
        "head_sha": sha,
        "tracked_worktree_dirty": dirty,
        "scope": "CONFIGURED_REPOSITORY_ONLY",
        "git_checks": "LOCAL_ONLY_NOT_CI_OR_MERGE_PROOF",
    }


def _owner_status(state_root: Path, release_sha: str) -> dict[str, Any]:
    """Ask the existing signed controller; do not issue commands or reveal keys."""
    if not _SHA.fullmatch(release_sha):
        return {"status": "UNVERIFIED", "reason": "EXPLICIT_RELEASE_SHA_REQUIRED",
                "authenticated_owner_permission": False}
    try:
        decision = owner_control.evaluate(
            owner_control.control_root(state_root), release_sha=release_sha
        )
    except Exception:  # noqa: BLE001 - credential or OS failures always deny
        return {"status": "UNVERIFIED", "reason": "OWNER_ORACLE_UNAVAILABLE",
                "authenticated_owner_permission": False}
    return {
        "status": decision.status,
        "action": decision.action,
        "generation": decision.generation,
        "reasons": list(decision.reasons),
        "revocation_acknowledged": decision.revocation_acknowledged,
        "authenticated_owner_permission": decision.permitted,
        "release_sha": release_sha,
    }


def run_readonly_command(
    command: str, *, state_root: Path, repo_root: Path, release_sha: str = ""
) -> dict[str, Any]:
    """Dispatch one exact read-only operation, with no user-specified arguments.

    No result from this surface grants permission to act, even when the
    authenticated owner oracle reports a currently live Take the Wheel grant.
    """
    if command not in _COMMANDS:
        return {"status": "REFUSED", "reason": "COMMAND_NOT_ALLOWLISTED",
                "can_execute": False}
    if not state_root.is_absolute() or not repo_root.is_absolute():
        return {"status": "REFUSED", "reason": "ABSOLUTE_HOST_ROOTS_REQUIRED",
                "can_execute": False}
    if command == "engine_status":
        data = coordination.engine_snapshot(state_root)
        source = "REPORTED_STATUS_NOT_OWNER_AUTHORITY"
    elif command == "owner_status":
        data = _owner_status(state_root, release_sha)
        source = "EXISTING_SIGNED_OWNER_ORACLE_READ_ONLY"
    else:
        data = _repository_head(repo_root)
        source = "FIXED_LOCAL_GIT_READ"
    return {
        "status": data.get("status", "UNAVAILABLE"),
        "command": command,
        "data": data,
        "source": source,
        "can_execute": False,
        "advisory_only": True,
    }


def readonly_capabilities() -> dict[str, Any]:
    """Static capability inventory, not client-reported privileges."""
    return {
        "status": "OK", "transport": "LOCAL_STDIO_ONLY",
        "commands": sorted(_COMMANDS), "generic_shell": False,
        "network_listener": False, "file_write": False,
        "can_execute": False, "advisory_only": True,
    }
