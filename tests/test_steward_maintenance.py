from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAINTENANCE = ROOT / "scripts" / "steward_maintenance.ps1"
INSTALLER = ROOT / "scripts" / "install_steward_maintenance.ps1"


def test_maintenance_cleanup_is_narrow_and_auditable():
    text = MAINTENANCE.read_text(encoding="utf-8")
    assert 'Extension -in @(".log", ".jsonl", ".tmp")' in text
    assert "PortfolioIdleWorker\\logs" in text
    assert "removed_owned_files" in text
    assert "Remove-Item -LiteralPath $full" in text
    assert "npm cache clean" not in text
    assert "pip cache purge" not in text
    assert "Get-ChildItem C:\\" not in text


def test_startup_repairs_only_proven_stale_actor_lock():
    text = MAINTENANCE.read_text(encoding="utf-8")
    assert "laptop_actor_bootstrap.py" in text
    assert "LIVE_LOCK" in text
    assert "STALE_LOCK_REMOVED" in text
    assert "Remove-Item -LiteralPath $lock" in text


def test_pre_reboot_uses_existing_reboot_after_maintenance():
    text = MAINTENANCE.read_text(encoding="utf-8")
    assert 'Mode -eq "PreReboot"' in text
    assert "PRE_REBOOT_MAINTENANCE" in text
    assert "Restart-Computer -Force" in text
    assert "NoRestart" in text


def test_installer_updates_existing_daily_reboot_and_adds_startup_sanity():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "Azathoth-Daily-Reboot" in text
    assert "Azathoth-Steward-Startup" in text
    assert "Set-ScheduledTask" in text
    assert "-TaskName $DailyRebootTask" in text
    assert "-Action $preRebootAction" in text
    assert "New-ScheduledTaskTrigger -AtLogOn" in text
    assert "MultipleInstances IgnoreNew" in text


def test_installer_has_non_admin_startup_and_reboot_fallbacks():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "startup-folder-fallback" in text
    assert "Azathoth-Steward-Startup.cmd" in text
    assert "existing-reboot-preserved" in text
    assert "daily_hygiene_authority" in text
    assert "StartupCheck" in text
