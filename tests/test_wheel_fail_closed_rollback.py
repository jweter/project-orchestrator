"""Regression tests for owner-control rollback after failed wheel start."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WHEEL = ROOT / "scripts" / "desktop-control" / "Take-The-Wheel.ps1"
PS = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(PS is None, reason="PowerShell is required")


def test_wheel_has_rollback_after_real_release_but_not_before_confirmation():
    source = WHEEL.read_text(encoding="utf-8")
    # The owner grant is the authenticated controller command, recorded only
    # after the release, Rocksmith and guard checks; nothing reads the retired marker.
    release = source.index("'issue','--action','TAKE_THE_WHEEL'")
    rollback_function = source.index("function Restore-OwnerAfterResumeFailure")
    assert rollback_function < release
    assert source.index("CONTROL_GUARD_MISSING") < release
    assert source.index("Rocksmith opened during confirmation") < release
    assert source.index("Set-IronGodLegacyGuardianSignal $state $false") > release
    assert "Get-Content -LiteralPath $marker" not in source
    assert "human-mode.json" not in source
    assert "Enter-HumanMode.ps1" in source
    assert "-KeepRemote" in source
    assert 'OWNER_ROLLBACK_CONFIRMED_REMOTE_PRESERVED' in source
    assert "Restore-OwnerAfterResumeFailure" in source[source.index("START_FAILED"):]
    assert "POST_RELEASE_PIN_MISMATCH" in source
    assert "TAKE_THE_WHEEL_RUNTIME_NOT_VERIFIED" in source
    assert "OWNER_RECLAIMED_DURING_STARTUP" in source
    assert source.index("Check-Development-ResumeReady.ps1") < release


@pytest.mark.parametrize("helper_exit,expected_exit,expect_owner", [
    (0, 6, True),
    (3, 8, True),
])
def test_isolated_resume_failure_handoff_returns_nonzero_and_retains_remote(
    tmp_path, helper_exit, expected_exit, expect_owner
):
    """Exercise the rollback function with an isolated non-operational helper."""
    source = WHEEL.read_text(encoding="utf-8")
    start = source.index("function Write-WheelLog(")
    stop = source.index("if($SelfTest){", start)
    fn = "$script:controllerLoaded=$false\n" + source[start:stop]

    base = tmp_path / "local" / "ExponentialEngine"
    state = base / "flow-state"
    state.mkdir(parents=True)
    marker = state / "human-mode.json"
    marker.write_text('{"enabled":false}', encoding="utf-8")
    helper = base / "Enter-HumanMode.ps1"
    helper.write_text(
        '$marker=Join-Path $env:LOCALAPPDATA "ExponentialEngine\\flow-state\\human-mode.json"\n'
        'Set-Content -Path $marker -Value \'{"enabled":true}\' -Encoding UTF8\n'
        f"exit {helper_exit}\n",
        encoding="utf-8",
    )
    harness = tmp_path / "invoke-rollback.ps1"
    harness.write_text(
        '$root=Join-Path $env:LOCALAPPDATA "ExponentialEngine"\n'
        '$state=Join-Path $root "flow-state"\n'
        '$log=Join-Path $state "control-switch.log"\n'
        + fn
        + '\nRestore-OwnerAfterResumeFailure "INJECTED_START_FAILED" 6\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(base.parent)
    process = subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(harness)], env=env, text=True, capture_output=True,
        timeout=25,
    )
    assert process.returncode == expected_exit, process.stdout + process.stderr
    assert json.loads(marker.read_text(encoding="utf-8-sig"))["enabled"] is expect_owner
    assert "INJECTED_START_FAILED" in (state / "control-switch.log").read_text()
    # This fake helper intentionally neither touches scheduled tasks nor closes
    # the real Remote Desktop Commander session.


def test_wheel_success_requires_a_permitting_execution_decision_after_resume():
    """Resident supervisor + actor are not authority; success also needs the
    supervisor's own permitting decision recorded after this resume began."""
    source = WHEEL.read_text(encoding="utf-8")
    started = source.index("$resumeStartedUtc=[DateTimeOffset]::UtcNow")
    task_start = source.index("foreach($name in @('ExponentialEngine-ContinuousFlow-Watchdog'")
    loop = source.index("while((Get-Date) -lt $deadline)")
    confirmed = source.index("TAKE_THE_WHEEL_CONFIRMED")
    not_authorized = source.index("Restore-OwnerAfterResumeFailure 'TAKE_THE_WHEEL_EXECUTION_NOT_AUTHORIZED' 6")
    assert started < task_start < loop < not_authorized < confirmed
    window = source[loop:not_authorized]
    assert "supervisor-execution-authority.json" in window
    assert "$execution.permitted -is [bool] -and $execution.permitted -eq $true" in window
    assert "$decidedAt -ge $resumeStartedUtc" in window
    assert "if($runtimeVerified -and $executionAuthorized -and $controllerPermits){break}" in window
    assert "'status','--release-sha'" in window
    assert "[int]$decision.Result.generation -eq $generation" in window
    assert "execution_authorized=$executionAuthorized" in source


def test_wheel_script_parses_without_powershell_errors():
    script = (
        "$errors=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{WHEEL}',[ref]$null,[ref]$errors) | Out-Null;"
        "if($errors){$errors | ForEach-Object { $_.Message }; exit 1}"
    )
    process = subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-Command", script],
        text=True, capture_output=True, timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr


def test_wheel_grant_requires_supervisor_acknowledgment_before_runtime_proof():
    source = WHEEL.read_text(encoding="utf-8")
    grant = source.index("'issue','--action','TAKE_THE_WHEEL'")
    tasks = source.index("foreach($name in @('ExponentialEngine-ContinuousFlow-Watchdog'")
    ack = source.index("'wait-ack','--generation'")
    loop = source.index("while((Get-Date) -lt $deadline)")
    confirmed = source.index("TAKE_THE_WHEEL_CONFIRMED")
    assert grant < tasks < ack < loop < confirmed
    assert "TAKE_THE_WHEEL_SUPERVISOR_ACK_MISSING" in source[ack:loop]
    assert "TAKE_THE_WHEEL_REFUSED_BY_CONTROLLER" in source[grant:tasks]
    # The controller bridge is loaded from the pinned release, never a local copy.
    assert "Join-Path ([string]$pinned.accepted_repo) 'scripts\\desktop-control\\IronGodOwnerControl.ps1'" in source
