"""Fail-closed release staging tests: must never promote from a non-green HEAD."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts"/"desktop-control"/"Stage-VerifiedLocalRelease.ps1"
PS=shutil.which("powershell.exe")
pytestmark=pytest.mark.skipif(PS is None, reason="Windows host required")


def run(tmp_path, extra_args=()):
    root=tmp_path/"local"
    (root/"flow-state").mkdir(parents=True,exist_ok=True)
    owner=root/"flow-state"/"human-mode.json"
    if not owner.exists():
        owner.write_text('{"enabled":true}',encoding="utf-8")
    command=[PS,"-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass",
             "-File",str(SCRIPT),"-SourceRepo",str(ROOT),"-Base",str(root),
             *extra_args]
    return subprocess.run(command, capture_output=True,text=True,timeout=25), root


def test_staging_requires_explicit_owner_hold(tmp_path):
    from tests.owner_authority import grant_owner_authority

    root=tmp_path/"local"
    # A live, acknowledged owner grant means autonomy is authorized: staging
    # must refuse while it exists (the retired marker is no longer an input).
    grant_owner_authority(root/"flow-state")
    p,_=run(tmp_path,("-Apply",))
    assert p.returncode != 0
    assert "OWNER_CONTROL_MUST_REMAIN_HELD" in p.stdout+p.stderr
    assert not (root/"releases").exists()
    assert not (root/"release-receipts").exists()


def test_release_stage_never_mutates_tasks_or_live_owner_state_by_design():
    source=SCRIPT.read_text(encoding="utf-8")
    assert "VERIFIED_STAGED" in source
    assert "LATEST_EXACT_HEAD_NATIVE_PREFLIGHT_NOT_GREEN" in source
    assert "NEW_LOCAL_RELEASE_MUST_DESCEND_FROM_CURRENT_PIN" in source
    assert "STAGING_ONLY_NO_RUNTIME_DEPLOYMENT" in source
    assert "output_sha256" in source
    assert "set_local_runtime_access.ps1" in source
    assert "Set-ScheduledTask" not in source
    assert "Register-ScheduledTask" not in source
    assert "Start-ScheduledTask" not in source
    assert "Enable-ScheduledTask" not in source
    assert "Remove-Item -LiteralPath $release" not in source


def test_stage_script_has_valid_powershell_syntax():
    expression = (
        "$tokens=$null;$errors=$null;"
        "[Management.Automation.Language.Parser]::ParseFile("
        f"'{str(SCRIPT).replace(chr(39), chr(39)*2)}',"
        "[ref]$tokens,[ref]$errors)|Out-Null;"
        "if($errors.Count -gt 0){exit 2}else{exit 0}"
    )
    p=subprocess.run([PS,"-NoProfile","-NonInteractive","-Command",expression],
                     capture_output=True,text=True,timeout=15)
    assert p.returncode == 0, p.stderr
