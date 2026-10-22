"""Read-only Iron God desktop-control commissioning audit.

This is evidence, never an authorization service or desktop control launcher.
It does not read, create, interpret, or modify the retired owner-control file.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

SCRIPT_PATHS = {
    "launcher": "DesktopControlSilent.vbs",
    "pause": "Enter-HumanMode.ps1",
    "resume": "Take-The-Wheel.ps1",
    "readiness": "Check-Development-ResumeReady.ps1",
    "watchdog": "Watch-ContinuousFlow.ps1",
    "mode_core": "ModeController/ModeCore.ps1",
    "mode_setter": "ModeController/Set-IronGodMode.ps1",
}
RETIRED_REFERENCE = "human-mode.json"
UNKNOWN = "UNKNOWN"

# Source-presence probes only: these do NOT establish execution authority,
# release verification, a functioning launcher or a correct runtime.
REQUIRED_RUNTIME_CAPABILITIES = {
    "worker_failure_diagnostics": (
        "src/project_orchestrator/supervisor_runtime.py",
        "def worker_exception_diagnostic(",
    ),
    "desktop_control_readiness": (
        "src/project_orchestrator/iron_god_desktop_readiness.py",
        "def audit_desktop_control(",
    ),
}


def _read_json(path: Path, issues: list[str], label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError, TypeError):
        issues.append(f"{label}_UNREADABLE")
        return {}
    if not isinstance(value, dict):
        issues.append(f"{label}_INVALID")
        return {}
    return value


def _heartbeat_fresh(
    heartbeat: Mapping[str, Any], now: datetime, *, max_age_seconds: int = 120
) -> bool:
    try:
        timestamp = datetime.fromisoformat(
            str(heartbeat.get("heartbeat_at_utc", "")).replace("Z", "+00:00")
        )
        if timestamp.tzinfo is None:
            return False
        seconds = (now - timestamp.astimezone(UTC)).total_seconds()
        return (
            0 <= seconds <= max_age_seconds
            and heartbeat.get("status") == "RUNNING_CURRENT"
        )
    except (ValueError, TypeError, OverflowError):
        return False


def inspect_pinned_release_sources(
    pin: Mapping[str, Any], issues: list[str]
) -> dict[str, bool]:
    """Read-only source-presence checks for merged capabilities in the PINNED release.

    This is descriptive version-drift evidence, not proof of verified release,
    runtime module loading, owner consent or authority to run a worker.
    """
    repo_value = pin.get("accepted_repo")
    if not isinstance(repo_value, str) or not repo_value.strip():
        return {}
    release = Path(repo_value)
    try:
        if (
            not release.is_absolute()
            or not release.is_dir()
            or release.is_symlink()
            or (hasattr(release, "is_junction") and release.is_junction())
        ):
            issues.append("PINNED_RELEASE_SOURCE_UNAVAILABLE")
            return {}
    except OSError:
        issues.append("PINNED_RELEASE_SOURCE_UNAVAILABLE")
        return {}

    observed: dict[str, bool] = {}
    for capability, (relative, token) in REQUIRED_RUNTIME_CAPABILITIES.items():
        source = release / relative
        try:
            # A reparse point or link in an ancestor must not redirect the
            # diagnostic outside the pinned checkout.
            resolved = source.resolve(strict=True)
            if (
                source.is_symlink()
                or not resolved.is_relative_to(release.resolve(strict=True))
                or not source.is_file()
                or source.stat().st_size > 512_000
            ):
                present = False
            else:
                present = token in source.read_text(encoding="utf-8")
        except (OSError, UnicodeError, RuntimeError):
            present = False
        observed[capability] = present
        if not present:
            issues.append(f"PINNED_RELEASE_CAPABILITY_MISSING:{capability}")
    return observed


def audit_desktop_control(
    *,
    base: Path,
    controller_file: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Inspect only source text and advisory runtime records; never trigger a transition.

    Files are never treated as sufficient proof of platform authorization, owner
    intent or a live, process-identity-bound handoff. Even a clean audit is
    descriptive only and cannot unlock a protected controller.
    """
    at = now or datetime.now(UTC)
    if at.tzinfo is None:
        raise ValueError("AUDIT_TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
    issues: list[str] = []
    for label, relative in SCRIPT_PATHS.items():
        try:
            text = (base / relative).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            issues.append(f"SCRIPT_UNREADABLE:{label}")
            continue
        if RETIRED_REFERENCE in text:
            issues.append(f"RETIRED_REFERENCE:{label}")

    state_root = base / "flow-state"
    mode = _read_json(state_root / "iron-god-desktop-mode.json", issues, "MODE")
    heartbeat = _read_json(
        state_root / "supervisor-generation-heartbeat.json", issues, "HEARTBEAT"
    )
    pin = _read_json(state_root / "supervisor-pinned-release.json", issues, "PIN")
    controller = _read_json(controller_file, issues, "CONTROLLER")
    installed_capabilities = inspect_pinned_release_sources(pin, issues)

    if mode.get("acknowledged_by_engine") is not True:
        issues.append("ENGINE_ACKNOWLEDGEMENT_NOT_PROVEN")
    if mode.get("effective_remote_authorization") in (None, "", "NONE"):
        issues.append("EFFECTIVE_REMOTE_AUTHORIZATION_NOT_PROVEN")

    if controller.get("mode") == "shadow_state_only":
        issues.append("CONTROLLER_SHADOW_MODE")
    if controller.get("candidate_execution_authorized") is not True:
        issues.append("CANDIDATE_EXECUTION_NOT_AUTHORIZED")

    pin_sha = pin.get("accepted_sha")
    heartbeat_sha = heartbeat.get("actor_revision")
    if not (
        isinstance(pin_sha, str)
        and len(pin_sha) == 40
        and all(char in "0123456789abcdef" for char in pin_sha)
        and heartbeat_sha == pin_sha
    ):
        issues.append("PINNED_SUPERVISOR_REVISION_UNVERIFIED")
    if not _heartbeat_fresh(heartbeat, at.astimezone(UTC)):
        issues.append("FRESH_SUPERVISOR_HEARTBEAT_UNVERIFIED")

    # A heartbeat or advisory mode request is not an owner-controlled,
    # process-bound acknowledgment. Never report it as operational authority.
    issues.append("AUTHORITATIVE_OWNER_HANDOFF_UNVERIFIED")
    return {
        "schema_version": 1,
        "audit_kind": "NONEXECUTING_READ_ONLY_DIAGNOSTIC",
        "status": "OWNER_CONTROL_UNVERIFIED",
        "transition_performed": False,
        "candidate_execution_authorized_by_audit": False,
        "shortcut_click_test_performed": False,
        "observed_supervisor_sha": heartbeat_sha
        if isinstance(heartbeat_sha, str)
        else UNKNOWN,
        "pinned_supervisor_sha": pin_sha if isinstance(pin_sha, str) else UNKNOWN,
        "pinned_release_capabilities": installed_capabilities,
        "reasons": sorted(set(issues)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", ".")) / "ExponentialEngine",
    )
    parser.add_argument("--controller", type=Path, default=None)
    args = parser.parse_args(argv)
    controller = (
        args.controller or args.base / "IronGodLocal" / "controller-handoff-state.json"
    )
    print(
        json.dumps(
            audit_desktop_control(base=args.base, controller_file=controller), indent=2
        )
    )
    return 2  # A read-only diagnostic never issues execution or owner-control approval.


if __name__ == "__main__":
    raise SystemExit(main())
