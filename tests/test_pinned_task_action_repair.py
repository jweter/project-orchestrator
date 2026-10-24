"""Safety regressions for disabled-only Iron God task-pin reconciliation."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "desktop-control" / "Repair-DisabledPinnedTasks.ps1"
READINESS = ROOT / "scripts" / "desktop-control" / "Check-Development-ResumeReady.ps1"
PS = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(PS is None, reason="Windows PowerShell required")


def run_script(state: Path, *, apply: bool = False):
    cmd = [
        PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(SCRIPT), "-StateRoot", str(state),
    ]
    if apply:
        cmd.append("-Apply")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=25)


@pytest.mark.parametrize("apply", [False, True])
def test_missing_owner_authority_fails_before_any_task_mutation(tmp_path, apply):
    state = tmp_path / "no-owner"
    state.mkdir()
    result = run_script(state, apply=apply)
    assert result.returncode != 0
    assert "MISSING_REQUIRED_RECORD" in (result.stdout + result.stderr)
    assert list(state.iterdir()) == []


@pytest.mark.parametrize("enabled", [None, "false", False])
def test_release_or_corrupt_owner_authority_refuses_repair(tmp_path, enabled):
    state = tmp_path / "owner-state"
    state.mkdir()
    if enabled == "false":
        (state / "human-mode.json").write_text("{invalid", encoding="utf-8")
    else:
        (state / "human-mode.json").write_text(
            json.dumps({"enabled": enabled}), encoding="utf-8"
        )
    result = run_script(state, apply=True)
    assert result.returncode != 0
    assert "VERIFIED_STAGED" not in (result.stdout + result.stderr)
    assert not list(state.glob("task-action-pin-repair-*"))


def test_owner_held_but_missing_pins_refuses_before_any_task_query(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "human-mode.json").write_text('{"enabled":true}', encoding="utf-8")
    result = run_script(state, apply=True)
    assert result.returncode != 0
    assert "MISSING_REQUIRED_RECORD" in (result.stdout + result.stderr)
    assert not list(state.glob("task-action-pin-repair-*"))


def test_repair_script_always_snapshots_before_mutation_and_never_starts_tasks():
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.index("Export-ScheduledTask") < text.index("Set-ScheduledTask")
    assert text.index("Ensure-OwnerHeld") < text.index("New-ScheduledTaskAction")
    assert "Register-ScheduledTask" in text  # rollback
    assert "Start-ScheduledTask" not in text
    assert "Enable-ScheduledTask" not in text
    assert "Disable-ScheduledTask" not in text
    assert "Set-Content -LiteralPath $owner" not in text
    assert "Stop-Process" not in text


def test_readiness_checks_exact_task_paths_and_actor_checkout_integrity():
    text = READINESS.read_text(encoding="utf-8")
    assert "WATCHDOG_ACTION_RELEASE_MISMATCH" in text
    assert "ACTOR_ACTION_RELEASE_MISMATCH" in text
    assert "PINNED_ACTOR_HEAD_MISMATCH" in text
    assert "PINNED_ACTOR_DIRTY" in text
    assert "WorkingDirectory" in text
    assert "-StateRoot" in text
    assert "-PinPath" in text
