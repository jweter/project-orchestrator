"""Cross-layer regression for the disabled marker handback contract."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from project_orchestrator.human_ownership import human_mode_active
from scripts import network_self_heal


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "owner_control_gate.py"


@pytest.mark.parametrize("payload,blocked", [
    ('{"enabled":false}', False),
    ('{"enabled":true}', True),
    ('{"enabled":"false"}', True),
    ('{broken', True),
    ('{}', True),
])
def test_owner_gate_and_network_recovery_agree(tmp_path, monkeypatch, payload, blocked):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated"))
    (tmp_path / "human-mode.json").write_text(payload, encoding="utf-8")
    assert human_mode_active(tmp_path) is blocked
    assert network_self_heal.human_mode(tmp_path) is blocked
    result = subprocess.run(
        [sys.executable, str(GATE), "--state-root", str(tmp_path)],
        check=False, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == int(blocked)


def test_legacy_give_me_control_still_vetoes_network_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated"))
    (tmp_path / "human-mode.json").write_text('{"enabled":false}')
    (tmp_path / "give-me-control").touch()
    assert network_self_heal.human_mode(tmp_path) is True


def test_persistent_launcher_owner_veto_precedes_all_side_effects(tmp_path, monkeypatch):
    from scripts import launch_persistent_supervisor as launcher

    marker = tmp_path / "human-mode.json"
    marker.write_text('{"enabled":true}', encoding="utf-8")
    monkeypatch.setattr(launcher, "start_network_guardian", lambda *_: pytest.fail("guardian launched"))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_a, **_k: pytest.fail("supervisor launched"))
    monkeypatch.setattr(
        sys, "argv",
        ["launch_persistent_supervisor", "--repo-root", str(ROOT),
         "--workspace-root", str(tmp_path / "workspaces"), "--state-root", str(tmp_path)],
    )
    assert launcher.main() == 0
    assert not (tmp_path / "persistent-supervisor-launch.json").exists()
