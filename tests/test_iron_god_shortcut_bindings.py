"""Read-only desktop binding checks never mistake a legacy target for success."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_shortcut_bindings as bindings


def installed_rows():
    return [
        {
            "name": "1 - GIVE ME CONTROL.lnk",
            "exists": True,
            "target": r"C:\Windows\System32\wscript.exe",
            "arguments": (
                r'"C:\Users\jerem\AppData\Local\ExponentialEngine'
                r'\DesktopControlSilent.vbs" human'
            ),
        },
        {
            "name": "2 - TAKE THE WHEEL.lnk",
            "exists": True,
            "target": r"C:\Windows\System32\wscript.exe",
            "arguments": (
                r'"C:\Users\jerem\AppData\Local\ExponentialEngine'
                r'\DesktopControlSilent.vbs" wheel'
            ),
        },
    ]


def test_real_installed_routes_are_classified_as_not_operational():
    report = bindings.audit_shortcut_routes(installed_rows())
    assert report["routes"]["1 - GIVE ME CONTROL.lnk"] == "LEGACY_VBS_OWNER_CONTROL"
    assert report["routes"]["2 - TAKE THE WHEEL.lnk"] == "LEGACY_VBS_OWNER_CONTROL"
    assert set(report["routes"]) == set(bindings.CONTROL_ICONS)
    assert (
        "SHORTCUT_USES_RETIRED_CONTROL_ROUTE:1 - GIVE ME CONTROL.lnk"
        in report["reasons"]
    )
    assert report["operational"] is False
    assert report["status"] == "OWNER_CONTROL_UNVERIFIED"
    assert report["retired_owner_file_read"] is False
    assert report["scheduled_tasks_changed"] is False
    assert report["control_activated"] is False


@pytest.mark.parametrize("name", tuple(bindings.CONTROL_ICONS))
def test_missing_primary_control_is_an_explicit_failure(name):
    records = [x for x in installed_rows() if x["name"] != name]
    report = bindings.audit_shortcut_routes(records)
    assert "SHORTCUT_MISSING_OR_UNREADABLE:" + name in report["reasons"]
    assert report["operational"] is False


@pytest.mark.parametrize("name", tuple(bindings.CONTROL_ICONS))
def test_unverified_new_target_never_claims_success(name):
    rows = installed_rows()
    for row in rows:
        if row["name"] == name:
            row["target"] = r"C:\Users\jerem\GitHub\unknown-control.exe"
            row["arguments"] = ""
    report = bindings.audit_shortcut_routes(rows)
    assert "SHORTCUT_TARGET_NOT_ENGINE_VERIFIED:" + name in report["reasons"]
    assert report["operational"] is False


def test_duplicate_metadata_fails_closed():
    rows = installed_rows()
    rows.append(dict(rows[0]))
    report = bindings.audit_shortcut_routes(rows)
    assert "DUPLICATE_SHORTCUT_METADATA:1 - GIVE ME CONTROL.lnk" in report["reasons"]
    assert not report["operational"]


@pytest.mark.parametrize(
    "rows", [None, [False], [{"name": 123}], [{"name": "x" * 161}]]
)
def test_untrusted_shortcut_inventory_is_rejected(rows):
    with pytest.raises(ValueError, match="SHORTCUT_EVIDENCE_INVALID"):
        bindings.audit_shortcut_routes(rows)


def test_non_windows_probe_fails_closed(monkeypatch):
    monkeypatch.setattr(bindings.os, "name", "posix")
    with pytest.raises(RuntimeError, match="WINDOWS_SHORTCUT_INSPECTION_UNAVAILABLE"):
        bindings.read_windows_shortcut_routes()


def test_windows_reader_does_not_execute_a_shortcut(monkeypatch):
    monkeypatch.setattr(bindings.os, "name", "nt")
    invoked = []

    def fake_run(args, **kwargs):
        invoked.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(installed_rows()))

    monkeypatch.setattr(bindings.subprocess, "run", fake_run)
    rows = bindings.read_windows_shortcut_routes()
    assert rows == installed_rows()
    assert len(invoked) == 1
    args, kwargs = invoked[0]
    assert args[:3] == ["powershell.exe", "-NoProfile", "-NonInteractive"]
    assert "CreateShortcut" in args[4]
    assert "ConvertTo-Json" in args[4]
    assert "Start-Process" not in args[4]
    assert "Stop-Process" not in args[4]
    assert ".Run(" not in args[4]
    assert "ReadAllText" not in args[4]
    assert kwargs["timeout"] == 20


def test_failed_shell_query_is_not_success(monkeypatch):
    monkeypatch.setattr(bindings.os, "name", "nt")
    monkeypatch.setattr(
        bindings.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="private"
        ),
    )
    with pytest.raises(RuntimeError, match="WINDOWS_SHORTCUT_INSPECTION_FAILED"):
        bindings.read_windows_shortcut_routes()


def test_cli_never_authorizes_resume(monkeypatch, capsys):
    monkeypatch.setattr(bindings, "read_windows_shortcut_routes", installed_rows)
    assert bindings.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "OWNER_CONTROL_UNVERIFIED"
    assert report["operational"] is False
    assert report["control_activated"] is False
