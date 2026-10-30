"""Read-only inspection of Windows desktop shortcuts to Iron God controls.

Shortcut *targets* are evidence, never approval or owner control. This
module does not click an icon, run a target, update a schedule or read
retired owner-control state.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Mapping

CONTROL_ICONS: dict[str, str] = {
    "1 - GIVE ME CONTROL.lnk": "human",
    "2 - TAKE THE WHEEL.lnk": "wheel",
}
# Owner-facing runtime switching has exactly two supported controls.
# Other historical shortcuts are outside this contract and cannot silently
# serve as alternative engine launch paths.
# This script only constructs read-only .lnk COM objects and reads properties.
# No call to Run(), Exec(), Invoke(), scheduled-task change, or control script.
_SHORTCUT_QUERY = r"""
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$shell = New-Object -ComObject WScript.Shell
$names = @(
 '1 - GIVE ME CONTROL.lnk',
 '2 - TAKE THE WHEEL.lnk'
)
$items = @()
foreach ($name in $names) {
  $path = Join-Path $desktop $name
  if (-not [IO.File]::Exists($path)) {
    $items += [pscustomobject]@{name=$name; exists=$false; target=''; arguments=''}
    continue
  }
  $link = $shell.CreateShortcut($path)
  $items += [pscustomobject]@{
    name=$name; exists=$true
    target=[string]$link.TargetPath
    arguments=[string]$link.Arguments
  }
}
ConvertTo-Json -InputObject @($items) -Depth 3 -Compress
"""


def read_windows_shortcut_routes() -> list[dict[str, Any]]:
    """Obtain shortcut metadata; fail closed if COM or the read probe fails."""
    if os.name != "nt":
        raise RuntimeError("WINDOWS_SHORTCUT_INSPECTION_UNAVAILABLE")
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _SHORTCUT_QUERY,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("WINDOWS_SHORTCUT_INSPECTION_FAILED")
    try:
        rows = json.loads(result.stdout.lstrip("\ufeff"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("WINDOWS_SHORTCUT_INSPECTION_INVALID") from exc
    if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
        raise RuntimeError("WINDOWS_SHORTCUT_INSPECTION_INVALID")
    return rows


def audit_shortcut_routes(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Classify installed routes without executing them or granting readiness.

    Any purported alternate executable path is unverified. A matching older
    VBS launcher is a *known legacy route*, never a successful engine handoff.
    """
    if not isinstance(rows, list):
        raise ValueError("SHORTCUT_EVIDENCE_INVALID")
    by_name: dict[str, Mapping[str, Any]] = {}
    issues: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("SHORTCUT_EVIDENCE_INVALID")
        name = row.get("name")
        if not isinstance(name, str) or len(name) > 160:
            raise ValueError("SHORTCUT_EVIDENCE_INVALID")
        if name in by_name:
            issues.add("DUPLICATE_SHORTCUT_METADATA:" + name)
        by_name[name] = row

    observed: dict[str, str] = {}
    for name, action in CONTROL_ICONS.items():
        row = by_name.get(name)
        if row is None or row.get("exists") is not True:
            issues.add("SHORTCUT_MISSING_OR_UNREADABLE:" + name)
            observed[name] = "UNVERIFIED"
            continue
        target = row.get("target")
        arguments = row.get("arguments")
        if not isinstance(target, str) or not isinstance(arguments, str):
            issues.add("SHORTCUT_INVALID_METADATA:" + name)
            observed[name] = "UNVERIFIED"
            continue
        normalized_target = target.casefold().replace("/", "\\")
        normalized_args = arguments.casefold().replace("/", "\\")
        if (
            normalized_target.endswith("\\wscript.exe")
            and "\\desktopcontrolsilent.vbs" in normalized_args
            and normalized_args.rstrip().endswith(" " + action)
        ):
            observed[name] = "LEGACY_VBS_OWNER_CONTROL"
            issues.add("SHORTCUT_USES_RETIRED_CONTROL_ROUTE:" + name)
        else:
            observed[name] = "UNVERIFIED_TARGET"
            issues.add("SHORTCUT_TARGET_NOT_ENGINE_VERIFIED:" + name)

    return {
        "schema_version": 1,
        "audit_kind": "READ_ONLY_SHORTCUT_TARGET_INSPECTION",
        "operational": False,
        "status": "OWNER_CONTROL_UNVERIFIED",
        "control_activated": False,
        "retired_owner_file_read": False,
        "scheduled_tasks_changed": False,
        "routes": observed,
        "reasons": sorted(issues | {"LIVE_ENGINE_HANDOFF_NOT_PROVEN"}),
    }


def main() -> int:
    try:
        report = audit_shortcut_routes(read_windows_shortcut_routes())
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        report = {
            "schema_version": 1,
            "status": "OWNER_CONTROL_UNVERIFIED",
            "operational": False,
            "reasons": [str(exc)],
            "control_activated": False,
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2  # Inspecting shortcuts never issues owner-control authorization.


if __name__ == "__main__":
    raise SystemExit(main())
