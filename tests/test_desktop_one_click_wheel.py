"""Take the Wheel shortcut: one explicit icon click, no redundant modal."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VBS = ROOT / "scripts" / "desktop-control" / "DesktopControlSilent.vbs"
WHEEL = ROOT / "scripts" / "desktop-control" / "Take-The-Wheel.ps1"


def _branch(contents: str, mode: str, next_mode: str) -> str:
    return contents.split(f'ElseIf mode = "{mode}" Then', 1)[1].split(
        f'ElseIf mode = "{next_mode}" Then', 1
    )[0]


def test_owner_click_supplies_explicit_authorization_once():
    vbs = VBS.read_text(encoding="utf-8")
    wheel = _branch(vbs, "wheel", "testhuman")
    assert 'Take-The-Wheel.ps1' in wheel
    assert 'suffix = " -ExplicitUserAuthorization"' in wheel
    assert 'sh.Run(cmd, 0, True)' in vbs

    ps = WHEEL.read_text(encoding="utf-8")
    assert "[switch]$ExplicitUserAuthorization" in ps
    assert "if(-not $ExplicitUserAuthorization){" in ps
    assert "TAKE_THE_WHEEL_EXPLICIT_USER_AUTHORIZATION" in ps
    assert ps.index("Check-Development-ResumeReady.ps1") < ps.index(
        "if(-not $ExplicitUserAuthorization){"
    )


def test_both_owner_buttons_succeed_without_dialogs():
    vbs = VBS.read_text(encoding="utf-8")
    success = vbs.split("If result = 0 Then", 1)[1].split(
        "ElseIf mode = \"wheel\" And result = 5 Then", 1
    )[0]
    assert 'If mode <> "wheel" And mode <> "human" Then' in success
    assert "MsgBox" in success  # diagnostic modes still acknowledge success
    assert "End If" in success


def test_failure_messages_and_owner_safety_remain():
    vbs = VBS.read_text(encoding="utf-8")
    ps = WHEEL.read_text(encoding="utf-8")
    assert 'suffix = " -KeepRemote"' in vbs
    assert 'suffix = " -SelfTest"' in vbs
    assert 'mode = "wheel" And result = 6' in vbs
    assert "TAKE_THE_WHEEL_RUNTIME_NOT_VERIFIED" in ps
    assert "Restore-OwnerAfterResumeFailure" in ps
    assert "Rocksmith2014" in ps
    assert "OWNER_RECLAIMED_DURING_STARTUP" in ps
