"""Fail-closed tests for promoting *disabled* pinned local Iron God tasks."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts"/"desktop-control"/"Promote-DisabledLocalRelease.ps1"
PS=shutil.which("powershell.exe")
pytestmark=pytest.mark.skipif(PS is None, reason="Windows PowerShell required")
OLD_SHA="a"*40
NEW_SHA="b"*40


def invoke(base: Path, apply: bool):
    argv=[PS,"-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass",
          "-File",str(SCRIPT),"-NewSha",NEW_SHA,"-Base",str(base)]
    if apply:
        argv.append("-Apply")
    return subprocess.run(argv,capture_output=True,text=True,timeout=25)


def test_promote_rejects_missing_or_unverified_owner_before_any_task_writes(
    tmp_path,
):
    base=tmp_path/"local"
    state=base/"flow-state"
    state.mkdir(parents=True)
    (state/"supervisor-pinned-release.json").write_text(json.dumps({
        "enabled": True,"accepted_sha":OLD_SHA,"accepted_repo":str(base/"old")
    }))
    (state/"actor-local-promotion.json").write_text(json.dumps({
        "enabled": True,"accepted_sha":OLD_SHA,"accepted_repo":str(base/"old")
    }))
    for owner in (None, {"enabled": False}, {"enabled": "false"}):
        marker=state/"human-mode.json"
        if owner is None:
            marker.unlink(missing_ok=True)
        else:
            marker.write_text(json.dumps(owner),encoding="utf-8")
        proc=invoke(base,apply=True)
        assert proc.returncode != 0
        assert not list(state.glob("pinned-release-promotion-*"))
        assert not (base/"releases").exists()
    # A live owner grant (autonomy authorized) is refused before any write.
    from tests.owner_authority import grant_owner_authority

    grant_owner_authority(state)
    proc=invoke(base,apply=True)
    assert proc.returncode != 0
    assert not list(state.glob("pinned-release-promotion-*"))


def test_promote_script_preserves_rollback_and_never_grants_execution():
    text=SCRIPT.read_text(encoding="utf-8")
    assert "NEW_RELEASE_RECEIPT_UNVERIFIED" in text
    assert "NEW_RELEASE_IS_NOT_DESCENDANT_OF_CURRENT_PIN" in text
    assert "OWNER_CONTROL_CHANGED_DURING_CUTOVER" in text
    assert "Export-ScheduledTask" in text
    assert "Register-ScheduledTask" in text
    assert text.index("Export-ScheduledTask") < text.index("WritePin $pinFile")
    assert text.index("WritePin $actorPinFile") < text.index("$out=@(& powershell.exe")
    assert "Start-ScheduledTask" not in text
    assert "Enable-ScheduledTask" not in text
    assert "Disable-ScheduledTask" not in text
    assert "Stop-Process" not in text
    assert "human-mode.json') -Raw | ConvertFrom-Json" not in text  # owner is read, not rewritten


def test_promote_script_has_valid_powershell_parser_tree():
    literal=str(SCRIPT).replace("'","''")
    statement=(
        "$t=$null;$e=$null;"
        f"[Management.Automation.Language.Parser]::ParseFile('{literal}',"
        "[ref]$t,[ref]$e)|Out-Null;"
        "if($e.Count){exit 2}else{exit 0}"
    )
    result=subprocess.run(
        [PS,"-NoProfile","-NonInteractive","-Command",statement],
        capture_output=True,text=True,timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_promotion_installs_desktop_controls_from_the_verified_release_with_rollback():
    text=SCRIPT.read_text(encoding="utf-8")
    files="@('DesktopControlSilent.vbs','Take-The-Wheel.ps1','Enter-HumanMode.ps1','Check-Development-ResumeReady.ps1','Test-DesktopControlShortcuts.ps1')"
    assert "$desktopFiles="+files in text
    backup=text.index("Copy-Item -LiteralPath $installed -Destination (Join-Path $desktopBackup $file)")
    pins=text.index("WritePin $pinFile $newPin")
    install=text.index("Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $Base $file)")
    readiness=text.index("$readiness=Join-Path $Base 'Check-Development-ResumeReady.ps1'")
    rollback=text.index("Copy-Item -LiteralPath $saved -Destination (Join-Path $Base $file)")
    assert backup < pins < install < readiness < rollback
    assert "DESKTOP_CONTROL_INSTALL_UNVERIFIED" in text  # hash-checked copy
    assert "NEW_RELEASE_LACKS_OWNER_CONTROLLER" in text
    assert "(Get-IronGodOwnerStatus $state (Join-Path $release 'src')) -ceq 'AUTHORIZED'" in text
    assert "human-mode.json" not in text
