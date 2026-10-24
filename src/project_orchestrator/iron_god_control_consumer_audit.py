"""Read-only inventory of deployed Iron God desktop-control consumers.

This audits script source text without accessing the retired control-state
file, starting processes, editing launchers, changing task settings, or granting
execution authorization. A clean source scan is not sufficient to authorize
control cutover: current owner authority and supervisor acknowledgment remain
independently unverified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

LEGACY_CONTROL_FILENAME = "human-mode.json"  # Match references in script text only.
MAX_SCRIPT_BYTES = 512_000
CONTROL_CONSUMERS = (
    "DesktopControlSilent.vbs",
    "Enter-HumanMode.ps1",
    "Take-The-Wheel.ps1",
    "Watch-ContinuousFlow.ps1",
    "Check-Development-ResumeReady.ps1",
    "ModeController/ModeCore.ps1",
    "ModeController/Set-IronGodMode.ps1",
    "ModeController/Test-IronGodMode.ps1",
)


@dataclass(frozen=True)
class ConsumerEvidence:
    relative_path: str
    source_sha256: str | None
    legacy_reference_lines: tuple[int, ...]
    problem: str | None
    remote_stop_lines: tuple[int, ...] = ()


@dataclass(frozen=True)
class ControlSourceAudit:
    status: str
    controller_mode: str | None
    candidate_execution_authorized: bool | None
    consumers: tuple[ConsumerEvidence, ...]
    blockers: tuple[str, ...]
    execution_authorized: bool = False


def _inspect_source(root: Path, relative_path: str) -> ConsumerEvidence:
    path = root.joinpath(*relative_path.split("/"))
    try:
        if path.is_symlink() or not path.is_file():
            return ConsumerEvidence(relative_path, None, (), "SOURCE_MISSING_OR_LINKED")
        if path.stat().st_size > MAX_SCRIPT_BYTES:
            return ConsumerEvidence(relative_path, None, (), "SOURCE_OVERSIZE")
        contents = path.read_bytes()
        text = contents.decode("utf-8-sig")
    except (OSError, UnicodeError):
        return ConsumerEvidence(relative_path, None, (), "SOURCE_UNREADABLE")
    lines = tuple(
        index
        for index, line in enumerate(text.splitlines(), start=1)
        if LEGACY_CONTROL_FILENAME in line.casefold()
    )
    remote_stop_lines = tuple(
        index
        for index, line in enumerate(text.splitlines(), start=1)
        if not line.lstrip().startswith(("#", "'"))
        and (
            re.search(
                r"(?i)\b(?:stop|disable)-scheduledtask\b.*azathoth-remote-desktop-commander",
                line,
            )
            or re.search(r"(?i)\bstop-process\b.*\$r\.processid", line)
        )
    )
    return ConsumerEvidence(
        relative_path=relative_path,
        source_sha256=hashlib.sha256(contents).hexdigest(),
        legacy_reference_lines=lines,
        problem=None,
        remote_stop_lines=remote_stop_lines,
    )


def audit_control_consumers(root: Path, *, controller: object) -> ControlSourceAudit:
    """Inventory fixed, known script consumers; never read owner-control state.

    Controller input is read independently from controller-handoff-state.json.
    Any reported healthy source inventory remains uncommissioned, not permission
    to run workers or to remove the protected candidate-execution gate.
    """
    consumers = tuple(_inspect_source(root, name) for name in CONTROL_CONSUMERS)
    blockers: list[str] = []
    for item in consumers:
        if item.problem:
            blockers.append(f"{item.relative_path}:{item.problem}")
        if item.legacy_reference_lines:
            numbers = ",".join(map(str, item.legacy_reference_lines))
            blockers.append(f"{item.relative_path}:RETIRED_CONTROL_REFERENCE:{numbers}")
        if item.remote_stop_lines:
            numbers = ",".join(map(str, item.remote_stop_lines))
            blockers.append(
                f"{item.relative_path}:REMOTE_TRANSPORT_STOP_PATH:{numbers}"
            )

    mode: str | None = None
    candidate_authorized: bool | None = None
    if not isinstance(controller, dict):
        blockers.append("CONTROLLER_STATE_UNREADABLE")
    else:
        if isinstance(controller.get("mode"), str):
            mode = controller["mode"]
        value = controller.get("candidate_execution_authorized")
        if type(value) is bool:
            candidate_authorized = value
        if mode != "candidate_mode" or candidate_authorized is not True:
            blockers.append("CONTROLLER_CANDIDATE_EXECUTION_DISABLED")

    blockers.append("CURRENT_OWNER_CONTROL_AND_SUPERVISOR_ACK_UNVERIFIED")
    source_blocked = any(
        item.problem or item.legacy_reference_lines or item.remote_stop_lines
        for item in consumers
    )
    return ControlSourceAudit(
        status="BLOCKED"
        if source_blocked
        or mode != "candidate_mode"
        or candidate_authorized is not True
        else "SOURCE_CONSUMERS_CLEAN_UNCOMMISSIONED",
        controller_mode=mode,
        candidate_execution_authorized=candidate_authorized,
        consumers=consumers,
        blockers=tuple(blockers),
        execution_authorized=False,
    )


def _load_controller_state(path: Path) -> object:
    """Only controller-handoff-state.json is in scope; no owner marker fallback."""
    if path.name.casefold() != "controller-handoff-state.json" or path.is_symlink():
        raise ValueError("CONTROLLER_PATH_NOT_AUTHORIZED_FOR_READ_ONLY_AUDIT")
    try:
        if not path.is_file() or path.stat().st_size > MAX_SCRIPT_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only deployed control-script audit; NEVER authorizes execution."
    )
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--controller-state", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        controller = _load_controller_state(args.controller_state)
    except ValueError as exc:
        parser.error(str(exc))
    audit = audit_control_consumers(args.engine_root, controller=controller)
    print(json.dumps(asdict(audit), sort_keys=True, indent=2))
    # Exit 3 for known production blockers; exit 2 if scanned sources are clean
    # but commissioning/owner-control evidence is still not proven.
    return 3 if audit.status == "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
