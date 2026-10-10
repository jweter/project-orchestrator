"""Self-contained smoke/regression tests for the isolated controller."""
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts' / 'independent_pr_controller.py'


def load_controller(monkeypatch, *, pr_cycle=None, failed_cycle=None, lock=True):
    scripts = types.ModuleType('scripts')
    scripts.__path__ = []
    merger = types.ModuleType('scripts.green_pr_merger')
    merger.cycle = pr_cycle or (lambda _: [])
    watcher = types.ModuleType('scripts.green_pr_watch_loop')
    watcher.acquire_singleton = lambda _: object() if lock else None
    watcher.release_singleton = lambda _: None
    watcher.process_cycle = lambda _state, _rows, _err, _start: {'kpi_events_emitted': 0}
    failures = types.ModuleType('scripts.failed_pr_accountability')
    failures.cycle = failed_cycle or (lambda *_: [])
    scripts.green_pr_merger = merger
    scripts.green_pr_watch_loop = watcher
    for name, mod in [(scripts.__name__, scripts), (merger.__name__, merger),
                      (watcher.__name__, watcher), (failures.__name__, failures)]:
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location('controller_test_module', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('content,status', [
    ('{"enabled": true}', 'OWNER_CONTROL_ACTIVE'),
    ('{"enabled": "false"}', 'OWNER_CONTROL_UNVERIFIED'),
    ('{corrupt', 'OWNER_CONTROL_UNVERIFIED'),
    ('{}', 'OWNER_CONTROL_UNVERIFIED'),
])
def test_owner_gate_rejects_unsafe_marker(tmp_path, monkeypatch, content, status):
    controller = load_controller(monkeypatch, pr_cycle=lambda _: pytest.fail('PR run must not start'))
    (tmp_path / 'human-mode.json').write_text(content)
    assert controller.reconcile(tmp_path, tmp_path) == {'status': status, 'acted': False}


def test_missing_marker_rejects_without_running(tmp_path, monkeypatch):
    controller = load_controller(monkeypatch, pr_cycle=lambda _: pytest.fail('PR run must not start'))
    assert controller.reconcile(tmp_path, tmp_path)['status'] == 'OWNER_CONTROL_UNVERIFIED'


def test_authorized_reconciliation_uses_existing_lock(tmp_path, monkeypatch):
    (tmp_path / 'human-mode.json').write_text(json.dumps({'enabled': False}))
    count = []
    controller = load_controller(monkeypatch, pr_cycle=lambda dry: count.append(dry) or [])
    result = controller.reconcile(tmp_path, tmp_path)
    assert result['status'] == 'RECONCILED'
    assert count == [False]


def test_existing_watcher_owns_lock_and_prevents_duplicate(tmp_path, monkeypatch):
    (tmp_path / 'human-mode.json').write_text(json.dumps({'enabled': False}))
    controller = load_controller(monkeypatch, lock=False, pr_cycle=lambda _: pytest.fail('duplicate'))
    assert controller.reconcile(tmp_path, tmp_path)['status'] == 'ALREADY_RUNNING'


def test_owner_change_before_gh_command_vetoes_mutation(tmp_path, monkeypatch):
    marker = tmp_path / 'human-mode.json'
    marker.write_text('{"enabled": false}')
    intercepted = []

    def pr_cycle(_):
        marker.write_text('{"enabled": true}')
        with pytest.raises(Exception, match='OWNER_CONTROL_ACTIVE'):
            subprocess.run(['gh', 'pr', 'merge', '123'], check=False)
        intercepted.append('blocked')
        return []

    controller = load_controller(monkeypatch, pr_cycle=pr_cycle)
    result = controller.reconcile(tmp_path, tmp_path)
    assert intercepted == ['blocked']
    assert result['status'] == 'OWNER_CONTROL_ACTIVE'
    assert result['acted'] is False

def test_controller_launches_children_without_console_on_windows(tmp_path, monkeypatch):
    from types import SimpleNamespace
    (tmp_path / "human-mode.json").write_text('{"enabled": false}', encoding="utf-8")
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    def pr_cycle(_):
        subprocess.run(["gh", "pr", "list"], check=False)
        return []

    controller = load_controller(monkeypatch, pr_cycle=pr_cycle)
    monkeypatch.setattr(controller.sys, "platform", "win32")
    monkeypatch.setattr(controller.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert controller.reconcile(tmp_path, tmp_path)["status"] == "RECONCILED"
    assert calls[0]["creationflags"] & 0x08000000


def test_installer_uses_no_console_pythonw():
    installer = (Path(__file__).resolve().parents[1] / "scripts" /
                 "install_independent_pr_controller.ps1").read_text(encoding="utf-8")
    assert "'pythonw.exe'" in installer
    assert "PYTHONW_REQUIRED_TO_AVOID_GAME_FOCUS_STEALING" in installer
