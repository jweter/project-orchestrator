from pathlib import Path

SCRIPT = Path("scripts/fallback/Watch-GitHubMailbox.ps1")


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def between(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def test_fallback_restart_checks_product_workers_before_stop():
    text = source()
    body = between(
        text,
        "function Invoke-SafeSupervisorRestart",
        "function Invoke-SafeMainDeploy",
    )
    assert "Get-ActiveProductWorkers" in body
    assert "DEFERRED_ACTIVE_PRODUCT_WORKERS" in body
    assert body.index("Get-ActiveProductWorkers") < body.index("Stop-Process")


def test_fallback_sync_checks_workers_before_actor_reset():
    text = source()
    body = between(
        text,
        "function Invoke-SafeActorSync",
        "function Invoke-SafeSupervisorRestart",
    )
    assert body.count("Get-ActiveProductWorkers") >= 2
    assert "REJECTED_ACTOR_REPO_DIRTY" in body
    assert body.index("Get-ActiveProductWorkers") < body.index("reset --hard origin/main")


def test_deferred_fallback_command_remains_retryable():
    text = source()
    assert "function Publish($o,[bool]$Consume=$true)" in text
    assert 'status="DEFERRED_ACTIVE_PRODUCT_WORKERS";consume=$false' in text
    assert "Publish $receipt ([bool]$result.consume)" in text


def test_atomic_safe_deploy_is_allowlisted():
    text = source()
    assert '"DEPLOY_MAIN_SAFE"' in text
    body = between(text, "function Invoke-SafeMainDeploy", "while($true)")
    assert "Invoke-SafeActorSync" in body
    assert "Invoke-SafeSupervisorRestart" in body


def test_phone_control_actions_are_explicitly_allowlisted():
    text = source()
    for action in ("RESTART_REMOTE_CONTROL", "RECOVER_CONTROL_PLANE", "REBOOT_WINDOWS"):
        assert f'"{action}"' in text


def test_remote_restart_uses_scheduled_task_not_arbitrary_shell():
    text = source()
    body = between(text, "function Invoke-RemoteControlRestart", "function Invoke-ControlPlaneRecovery")
    assert "Get-ScheduledTask" in body
    assert "Enable-ScheduledTask" in body
    assert "Start-ScheduledTask" in body
    assert "Invoke-Expression" not in body


def test_reboot_is_fixed_command_with_audit_reason():
    text = source()
    assert 'shutdown.exe /r /t 15 /d p:4:1 /c "Azathoth authenticated control-plane reboot"' in text
    assert 'status="REBOOT_ACCEPTED"' in text


def test_phone_control_records_progress_history_and_human_mode():
    text = source()
    for status in ("ACKNOWLEDGED", "EXECUTING"):
        assert f'Write-Progress $c.command_id $c.action "{status}"' in text
    assert "Append-History" in text
    assert '"HUMAN_MODE"' in text and '"FULL_POWER"' in text
    assert 'Set-Content -Encoding UTF8 $humanMode' in text
    assert 'REJECTED_USE_TAKE_THE_WHEEL' in text


def test_fallback_sync_rejects_unpushed_actor_commits_before_reset():
    text = source()
    body = between(
        text,
        "function Invoke-SafeActorSync",
        "function Invoke-SafeSupervisorRestart",
    )
    assert "rev-list origin/main..HEAD" in body
    assert "REJECTED_ACTOR_UNPUSHED_COMMITS" in body
    assert "SYNC_HISTORY_CHECK_FAILED" in body
    assert body.index("fetch origin main") < body.index("rev-list origin/main..HEAD")
    assert body.index("rev-list origin/main..HEAD") < body.index("reset --hard origin/main")
