"""Desktop controls, watchdog and supervisor pacing wired to the owner controller."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from project_orchestrator import iron_god_owner_control as oc
from project_orchestrator import supervisor_runtime
from tests.owner_authority import TEST_RELEASE_SHA, grant_owner_authority, revoke_owner_authority

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "scripts" / "desktop-control"
BRIDGE = DESKTOP / "IronGodOwnerControl.ps1"
HUMAN = DESKTOP / "Enter-HumanMode.ps1"
READY = DESKTOP / "Check-Development-ResumeReady.ps1"
WATCHDOG = ROOT / "scripts" / "watch_pinned_continuous_flow.ps1"
PS = shutil.which("powershell.exe")
windows = pytest.mark.skipif(os.name != "nt" or PS is None, reason="Windows PowerShell required")


class Pool:
    def __init__(self, completions):
        self.completions = list(completions)
        self.waits = []

    def wait_for_completion(self, timeout):
        self.waits.append(timeout)
        return self.completions.pop(0) if self.completions else False


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_pacing_wakes_promptly_when_owner_reclaims():
    clock = Clock()
    pool = Pool([False, False, False])
    statuses = iter(["AUTHORIZED", "AUTHORIZED", "OWNER_CONTROL_ACTIVE"])

    def owner(_root):
        clock.now += 2.0
        return next(statuses)

    result = supervisor_runtime.wait_for_completion_or_reclaim(
        pool, 300, Path("state"), owner_status_fn=owner, clock=clock
    )
    assert result == "OWNER_RECLAIMED"
    assert pool.waits == [2.0, 2.0, 2.0]


def test_pacing_still_wakes_on_worker_completion_and_times_out():
    clock = Clock()
    pool = Pool([True])
    assert supervisor_runtime.wait_for_completion_or_reclaim(
        pool, 300, Path("state"), owner_status_fn=lambda _r: "AUTHORIZED", clock=clock
    ) == "COMPLETED"
    clock = Clock()
    pool = Pool([])

    def owner(_root):
        clock.now += 2.0
        return "AUTHORIZED"

    assert supervisor_runtime.wait_for_completion_or_reclaim(
        pool, 5, Path("state"), owner_status_fn=owner, clock=clock
    ) == "TIMEOUT"
    assert pool.waits[-1] == pytest.approx(1.0)


def test_supervisor_paces_through_the_owner_interruptible_wait():
    source = (ROOT / "scripts" / "continuous_flow_supervisor.py").read_text(encoding="utf-8")
    assert "_supervisor_runtime.wait_for_completion_or_reclaim(worker_pool,delay,state)" in source
    assert "worker_pool.wait_for_completion(delay)" not in source


def _cli(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "project_orchestrator.iron_god_owner_control", *args],
        capture_output=True, text=True, timeout=60, check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), **(env or {})},
    )


@windows
def test_cli_owner_status_and_release_provenance(tmp_path):
    state = tmp_path / "flow-state"
    root = oc.control_root(state)
    assert _cli("--root", str(root), "owner-status").returncode == 1
    grant_owner_authority(state)
    granted = _cli("--root", str(root), "owner-status")
    assert granted.returncode == 0 and json.loads(granted.stdout)["owner_status"] == "AUTHORIZED"
    wrong = _cli("--expect-source", str(tmp_path / "elsewhere" / "src"), "--root", str(root), "owner-status")
    assert wrong.returncode == 4 and json.loads(wrong.stdout)["reason"] == "CONTROLLER_PROVENANCE_MISMATCH"
    right = _cli("--expect-source", str(ROOT / "src"), "--root", str(root), "owner-status")
    assert right.returncode == 0


def _isolated_engine(tmp_path):
    local = tmp_path / "local"
    state = local / "ExponentialEngine" / "flow-state"
    state.mkdir(parents=True)
    (state / "supervisor-pinned-release.json").write_text(json.dumps({
        "schema_version": 1, "enabled": True, "accepted_repo": str(ROOT),
        "accepted_sha": TEST_RELEASE_SHA, "receipt_path": str(tmp_path / "receipt.json"),
    }), encoding="utf-8")
    return local, state


def _ps(script, local):
    return subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=120, check=False,
        env={**os.environ, "LOCALAPPDATA": str(local)},
    )


@windows
def test_powershell_bridge_reads_only_the_controller(tmp_path):
    local, state = _isolated_engine(tmp_path)
    # A released retired marker is not authority for the bridge.
    (state / "human-mode.json").write_text('{"enabled": false}', encoding="utf-8")
    probe = f". '{BRIDGE}'; Get-IronGodOwnerStatus '{state}'"
    assert _ps(probe, local).stdout.strip() == "OWNER_CONTROL_UNVERIFIED"
    grant_owner_authority(state)
    assert _ps(probe, local).stdout.strip() == "AUTHORIZED"
    revoke_owner_authority(state)
    assert _ps(probe, local).stdout.strip() == "OWNER_CONTROL_ACTIVE"


@windows
def test_powershell_cannot_grant_outside_the_desktop_icon_but_can_revoke(tmp_path):
    local, state = _isolated_engine(tmp_path)
    pin = state / "supervisor-pinned-release.json"
    grant = _ps(
        f". '{BRIDGE}'; $r = Invoke-IronGodOwnerController -StateRoot '{state}' "
        f"-Arguments @('issue','--action','TAKE_THE_WHEEL','--pin','{pin}'); "
        "\"$($r.ExitCode) $($r.Result.reason)\"", local)
    code, reason = grant.stdout.strip().split(" ", 1)
    assert code == "3" and reason in {"NOT_DESKTOP_CONTROL_ORIGIN", "NOT_ACTIVE_CONSOLE_SESSION"}
    revoke = _ps(
        f". '{BRIDGE}'; $r = Invoke-IronGodOwnerController -StateRoot '{state}' "
        "-Arguments @('issue','--action','GIVE_ME_CONTROL'); \"$($r.ExitCode) $($r.Result.action)\"", local)
    assert revoke.stdout.strip() == "0 GIVE_ME_CONTROL"


@windows
def test_legacy_guardian_signal_is_write_only_and_non_authoritative(tmp_path):
    local, state = _isolated_engine(tmp_path)
    _ps(f". '{BRIDGE}'; Set-IronGodLegacyGuardianSignal '{state}' $false 'test'", local)
    signal = json.loads((state / "human-mode.json").read_text(encoding="utf-8-sig"))
    assert signal["enabled"] is False and signal["authority"] == "NONE_COMPATIBILITY_SIGNAL_ONLY"
    assert oc.evaluate(oc.control_root(state)).permitted is False
    bridge = BRIDGE.read_text(encoding="utf-8")
    assert bridge.count("human-mode.json") == 1  # only the write target


@windows
def test_readiness_requires_the_pinned_release_to_carry_the_controller(tmp_path):
    local, state = _isolated_engine(tmp_path)
    old_release = tmp_path / "old-release"
    (old_release / "src").mkdir(parents=True)
    pin = json.loads((state / "supervisor-pinned-release.json").read_text(encoding="utf-8"))
    pin["accepted_repo"] = str(old_release)
    (state / "supervisor-pinned-release.json").write_text(json.dumps(pin), encoding="utf-8")
    result = subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(READY)],
        capture_output=True, text=True, timeout=120, check=False,
        env={**os.environ, "LOCALAPPDATA": str(local)},
    )
    assert result.returncode == 5
    assert "PINNED_RELEASE_LACKS_OWNER_CONTROLLER" in json.loads(result.stdout)["issues"]
    assert "human-mode.json" not in READY.read_text(encoding="utf-8")


def test_give_me_control_revokes_before_stopping_and_spares_test_runs():
    source = HUMAN.read_text(encoding="utf-8")
    revoke = source.index("'issue','--action','GIVE_ME_CONTROL'")
    ack = source.index("'wait-ack','--generation'")
    kill = source.index("$targets = @(Find-OwnerWorkers)")
    assert revoke < ack < kill
    assert "'--timeout','20'" in source
    assert "SUPERVISOR_REVOCATION_ACKNOWLEDGED" in source
    assert "if($_.CommandLine -match '(?i)\\s-m\\s+pytest\\b') { return $false }" in source
    assert chr(8) not in source  # no control characters smuggled into a regex
    assert "Join-Path ([string]$pinned.accepted_repo) 'scripts\\desktop-control\\IronGodOwnerControl.ps1'" in source


def test_watchdog_gate_is_the_controller():
    source = WATCHDOG.read_text(encoding="utf-8")
    assert ". (Join-Path $PSScriptRoot 'desktop-control\\IronGodOwnerControl.ps1')" in source
    assert "(Get-IronGodOwnerStatus $StateRoot) -ceq 'AUTHORIZED'" in source
    assert "human-mode.json" not in source
