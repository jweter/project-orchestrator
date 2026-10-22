"""Verify owner-control receipts survive concurrent readers without changing mode."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PS = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(PS is None, reason="Windows PowerShell required")
CONTROL = Path(__file__).resolve().parents[1] / "scripts/desktop-control/Enter-HumanMode.ps1"


@pytest.mark.parametrize("lock", ["none", "reader", "exclusive"])
def test_atomic_receipt_preserves_safety_under_file_contention(tmp_path, lock):
    code = r"""
$ErrorActionPreference='Stop'
$state=$env:TEST_STATE_ROOT
$receipt=Join-Path $state 'receipt.json'
$events=New-Object 'System.Collections.Generic.List[string]'
$t=$null;$e=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:TEST_CONTROL_SOURCE,[ref]$t,[ref]$e)
$fn=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Save-ControlReceipt'},$true)
if(-not $fn -or $e){throw 'Receipt function unavailable'}
Invoke-Expression $fn.Extent.Text
[IO.File]::WriteAllText($receipt,'{"status":"old"}')
$handle=$null
try {
 if($env:TEST_LOCK_KIND -ne 'none'){
  $share=[IO.FileShare]::Read
  if($env:TEST_LOCK_KIND -eq 'reader'){$share=$share -bor [IO.FileShare]::Delete}
  $handle=[IO.File]::Open($receipt,[IO.FileMode]::Open,[IO.FileAccess]::Read,$share)
 }
 $saved=Save-ControlReceipt @{status='new'}
 $actual=(Get-Content $receipt -Raw|ConvertFrom-Json).status
 if($env:TEST_LOCK_KIND -eq 'exclusive'){
  if($saved -or $actual -ne 'old' -or $events.Count -ne 1){throw 'Unsafe locked receipt result'}
 } elseif(-not $saved -or $actual -ne 'new'){throw ('Atomic receipt update failed: '+($events -join '; '))}
} finally {if($handle){$handle.Dispose()}}
if(Get-ChildItem $state -Filter '*.tmp'){throw 'Temporary receipt leaked'}
"""
    env = {**os.environ, "TEST_CONTROL_SOURCE": str(CONTROL),
           "TEST_STATE_ROOT": str(tmp_path), "TEST_LOCK_KIND": lock}
    result = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", code],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
