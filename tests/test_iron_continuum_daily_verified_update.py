"""IRON CONTINUUM #747: daily self-update guardrail contract.

Scripts are portable artifacts; Linux CI verifies behavior-sensitive structure,
while Windows owner device runs PowerShell parser and self-test scenarios.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "desktop-control" / "IronGod-Daily-Verified-Update.ps1"
INSTALLER = ROOT / "scripts" / "desktop-control" / "Install-IronGod-Daily-UpdateTask.ps1"


def _texts() -> tuple[str, str]:
    return CHECKER.read_text(encoding="utf-8"), INSTALLER.read_text(encoding="utf-8")


def test_daily_checker_only_targets_verified_github_main_and_legacy_installer():
    script, _ = _texts()
    assert "$repository = 'jweter/project-orchestrator'" in script
    assert "$branch = 'main'" in script
    assert "git -c http.lowSpeedLimit=1024" in script
    assert "ls-remote --exit-code" in script
    assert "refs/heads/main" in script
    assert "supervisor-pinned-release.json" in script
    assert "tools\\VerifiedReleaseUpdater\\IronGod-Update.ps1" in script
    assert "& powershell.exe -NoProfile -NonInteractive" in script
    assert "if ($LASTEXITCODE -ne 0)" in script
    assert "GUARDED_INSTALL_FAILED_CODE" in script


def test_daily_checker_never_grants_owner_permission_or_stops_existing_workers():
    script, _ = _texts()
    assert "Get-IronGodOwnerStatus" in script
    assert "OWNER_CONTROL_ACTIVE" in script
    assert "Get-ScheduledTask -TaskName $name" in script
    assert "$task.State -ne 'Disabled'" in script
    assert "Get-CimInstance Win32_Process" in script
    assert "UPDATE_PENDING_OWNER_CONTROL" in script
    assert "UPDATE_PENDING_QUIESCENCE" in script
    assert "if ($CheckOnly)" in script
    assert "UPDATE_READY_DRY_RUN" in script
    assert "UPDATED_DISABLED_OWNER_RESUME_REQUIRED" in script
    for forbidden in (
        "Stop-ScheduledTask", "Disable-ScheduledTask",
        "Start-ScheduledTask", "Enable-ScheduledTask",
        "Restart-Computer", "Stop-Process", "Remove-Item -Recurse",
        "Take-The-Wheel.ps1", "Enter-HumanMode.ps1",
    ):
        assert forbidden not in script


def test_once_daily_windows_task_is_idempotent_no_immediate_install():
    script, installer = _texts()
    assert "New-ScheduledTaskTrigger -Daily" in installer
    assert "$At = '02:20'" in installer
    assert "-MultipleInstances IgnoreNew" in installer
    assert "-RunLevel Limited" in installer
    assert "-LogonType Interactive" in installer
    assert "Get-ScheduledTask -TaskName $taskName" in installer
    assert "ALREADY_REGISTERED" in installer
    assert "EXISTING_DAILY_TASK_DIFFERS_MANUAL_RECONCILIATION_REQUIRED" in installer
    assert "INSTALLED_DAILY_CHECK_DIFFERS_MANUAL_RECONCILIATION_REQUIRED" in installer
    assert "-File \"' + $destination + '\"" in installer
    assert "if ($Plan)" in installer
    assert "exit 0" in installer
    assert "-SelfTest" not in installer  # cannot run updater on install
    assert "Register-ScheduledTask" not in script
    assert "Register-ScheduledTask" in installer


def test_checker_reports_pinned_release_before_and_after_without_false_activation():
    script, _ = _texts()
    assert "installed_before = $installed" in script
    assert "latest_main_observed = $latest" in script
    assert "installed_after = $nowInstalled" in script
    assert "owner_grant_created = $false" in script
    assert "workers_stopped = $false" in script
    assert "task_triggered_engine_start = $false" in script
    assert "[IO.File]::Replace($scratch, $reportFile, $backup)" in script
    assert "SELFTEST_FIRST" in script and "SELFTEST_SECOND" in script
    assert "CHECK_OR_UPDATE_FAILED" in script
