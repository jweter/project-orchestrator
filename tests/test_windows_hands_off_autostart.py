from pathlib import Path
from scripts.windows_hands_off_autostart import BOOT_TASK_NAME,TASK_NAME,task_command

def test_task_command_uses_fail_closed_launcher():
    cmd=task_command(Path(r"C:\repo"),Path(r"C:\workspace"),Path(r"C:\repo\.hands-off-state"))
    assert "launch_persistent_supervisor.py" in cmd
    assert "continuous_flow_supervisor.py" not in cmd
    assert "--timeout-seconds 60" in cmd
    assert "--builder-seconds 60" in cmd
    assert "--merge-seconds 60" in cmd
    assert "--yield-on-interactive-session" not in cmd
    assert "--wait-for-takeover" in cmd
    boot=task_command(Path(r"C:\repo"),Path(r"C:\workspace"),Path(r"C:\repo\.hands-off-state"),boot_owner=True)
    assert "--yield-on-interactive-session" in boot

def test_task_name_is_stable():
    assert TASK_NAME=="ProjectHandsOffSupervisor"
    assert BOOT_TASK_NAME=="ProjectHandsOffSupervisorBoot"
