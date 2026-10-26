"""Give Me Control must stop autonomous work without severing remote support."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VBS = ROOT / "scripts" / "desktop-control" / "DesktopControlSilent.vbs"
HUMAN = ROOT / "scripts" / "desktop-control" / "Enter-HumanMode.ps1"
PS = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(PS is None, reason="Windows host required")


def test_owner_button_uses_explicit_keep_remote_mode():
    text = VBS.read_text(encoding="utf-8")
    human = text.split('If mode = "human" Then', 1)[1].split(
        'ElseIf mode = "wheel" Then', 1
    )[0]
    assert "stream.WriteLine" in human
    assert 'suffix = " -KeepRemote"' in human
    assert human.index("stream.WriteLine") < human.index("suffix =")
    assert "sh.Run(cmd, 0, True)" in text


def test_keep_remote_skips_bridge_watchdogs_but_not_engine_workers():
    source = HUMAN.read_text(encoding="utf-8")
    assert "$KeepRemote" in source
    assert "Stop-Process -Id $p.ProcessId" in source
    assert "Disable-ScheduledTask -TaskName $name" in source
    assert "$name -match '^Azathoth-Remote-'" in source
    assert "RemoteBridgeGuardian" in source
    assert "RemoteBridgeSupervisor" in source
    assert "Azathoth-Remote-Control-Watchdog" in source
    assert "$remainingRemote.Count -ne 0 -and -not $KeepRemote" in source
    assert "OWNER_CONTROL_GRANTED" in source


def test_owner_selftest_keeps_owner_marker_and_never_mutates_remote_tasks(tmp_path):
    root = tmp_path / "local-app-data"
    state = root / "ExponentialEngine" / "flow-state"
    state.mkdir(parents=True)
    original = '{"enabled":true,"source":"test-fixture"}'
    (state / "human-mode.json").write_text(original, encoding="utf-8")
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(root)
    result = subprocess.run(
        [
            PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(HUMAN), "-SelfTest",
        ],
        env=env, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "human-mode.json").read_text(encoding="utf-8") == original
    receipt = json.loads((state / "desktop-human-selftest.json").read_text(encoding="utf-8-sig"))
    assert receipt["non_destructive"] is True
    assert receipt["owner_active"] is True
    assert receipt["passed"] is True
    output = json.loads(result.stdout)
    # Audit-only self-test reports live workers accurately instead of falsely
    # claiming that a control handoff stopped unrelated production processes.
    assert output["status"] in {"OWNER_CONTROL_GRANTED", "PARTIAL"}
    if output["status"] == "PARTIAL":
        assert output["worker_count"] > 0 or output["running_task_names"]
    assert output["changes"] == []
    assert output["remote_disconnect_requested"] is False
