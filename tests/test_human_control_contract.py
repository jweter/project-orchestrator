from pathlib import Path


SUPERVISOR = Path("scripts/continuous_flow_supervisor.py")
INSTALLER = Path("scripts/install_continuous_flow.ps1")
FALLBACK = Path("scripts/fallback/Watch-GitHubMailbox.ps1")


def test_supervisor_checks_human_ownership_before_instance_and_merge_work():
    text = SUPERVISOR.read_text(encoding="utf-8")
    startup = text.index("if human_mode_active(state):")
    lock = text.index("instance_lock=acquire_supervisor_instance_lock(state,RUNTIME_LEASE_STATE.parent)")
    assert startup < lock

    loop = text.index("while True:")
    loop_gate = text.index("if human_mode_active(state):", loop)
    session_gate = text.index("if a.yield_on_interactive_session:", loop)
    assert loop_gate < session_gate
    assert 'status":"HUMAN_MODE_ACTIVE"' in text


def test_startup_launcher_refuses_autonomy_while_human_marker_exists():
    text = INSTALLER.read_text(encoding="utf-8")
    assert '$HumanMode = Join-Path $StateRoot "human-mode.json"' in text
    assert "'if exist \"' + $HumanMode + '\" exit /b 0'" in text
    assert "if (-not (Test-Path $HumanMode))" in text


def test_fallback_recovery_cannot_restart_supervisor_during_human_mode():
    text = FALLBACK.read_text(encoding="utf-8")
    restart = text[text.index("function Invoke-SafeSupervisorRestart"):text.index("function Invoke-RemoteControlRestart")]
    recovery = text[text.index("function Invoke-ControlPlaneRecovery"):text.index("function Invoke-SafeMainDeploy")]
    assert "if(Test-HumanMode)" in restart
    assert restart.index("if(Test-HumanMode)") < restart.index("Stop-Process")
    assert "if(Test-HumanMode)" in recovery
    assert "Invoke-SafeSupervisorKick" in recovery


def test_human_mode_stops_workers_and_full_power_explicitly_removes_lock():
    text = FALLBACK.read_text(encoding="utf-8")
    human = text[text.index('"HUMAN_MODE" {'):text.index('"FULL_POWER" {')]
    full = text[text.index('"FULL_POWER" {'):text.index('"REBOOT_WINDOWS" {')]
    assert "Set-Content -Encoding UTF8 $humanMode" in human
    assert "Set-WorkerTasksEnabled $false" in human
    assert "Stop-AutonomousProcesses" in human
    assert "Remove-Item -Force $humanMode" in full
    assert "Set-WorkerTasksEnabled $true" in full
    assert "Invoke-SafeSupervisorKick" in full


def test_local_human_mode_keeps_control_plane_and_dashboard_alive():
    text = Path("scripts/enter_human_mode.ps1").read_text(encoding="utf-8")
    assert "Azathoth-Remote-Desktop-Commander" not in text
    assert "Azathoth-Dashboard-Control" not in text
    assert "Guard-GitHubMailbox.ps1" not in text
    assert "foundry_dashboard_server.py" not in text
