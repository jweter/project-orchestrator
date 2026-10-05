from pathlib import Path

from scripts.hands_off_boot_entrypoint import guardian_command, launcher_command
from scripts.windows_hands_off_autostart import BOOT_TASK_NAME, TASK_NAME, task_command


def test_task_command_uses_boot_entrypoint():
    repo = Path(r"C:\repo")
    workspace = Path(r"C:\workspace")
    state = Path(r"C:\repo\.hands-off-state")

    logon = task_command(repo, workspace, state)
    assert "hands_off_boot_entrypoint.py" in logon
    assert "launch_persistent_supervisor.py" not in logon
    assert "--boot-owner" not in logon

    boot = task_command(repo, workspace, state, boot_owner=True)
    assert "hands_off_boot_entrypoint.py" in boot
    assert "--boot-owner" in boot


def test_boot_entrypoint_starts_guardian_and_preserves_launcher_authority():
    repo = Path(r"C:\repo")
    workspace = Path(r"C:\workspace")
    state = Path(r"C:\repo\.hands-off-state")

    guardian = guardian_command(repo, state)
    assert "network_self_heal.py" in guardian[1]
    assert "--allow-reboot" in guardian
    assert guardian[guardian.index("--failures") + 1] == "3"

    logon = launcher_command(repo, workspace, state, boot_owner=False)
    assert "launch_persistent_supervisor.py" in logon[1]
    assert "--wait-for-takeover" in logon
    assert "--yield-on-interactive-session" not in logon

    boot = launcher_command(repo, workspace, state, boot_owner=True)
    assert "--yield-on-interactive-session" in boot
    assert "--wait-for-takeover" not in boot


def test_task_name_is_stable():
    assert TASK_NAME == "ProjectHandsOffSupervisor"
    assert BOOT_TASK_NAME == "ProjectHandsOffSupervisorBoot"
