"""Exercise Windows bootstrap ordering without Docker or changing the host."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")


def run_setup(tmp_path: Path, *, ready_after: int, import_exit_code: int = 0) -> dict:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    n8n = tmp_path / "n8n"
    n8n.mkdir()
    setup = scripts / "setup-n8n.ps1"
    shutil.copyfile(REPO_ROOT / "scripts" / "setup-n8n.ps1", setup)
    shutil.copyfile(REPO_ROOT / "n8n" / ".env.example", n8n / ".env.example")
    quoted_setup = str(setup).replace("'", "''")
    command = f"""
$global:ProbeCalls = 0
$global:ImportCalls = 0
$global:ImportedBeforeReady = $false
$global:ProbeCode = ''
$global:Failure = $null
$initialLocation = (Get-Location).Path
function docker {{
    $global:LASTEXITCODE = 0
    $arguments = @($args)
    $operation = $arguments -join ' '
    if ($operation.StartsWith('compose exec -T n8n node -e ')) {{
        $global:ProbeCalls++
        $global:ProbeCode = $arguments[-1]
        if ($global:ProbeCalls -lt {ready_after}) {{ $global:LASTEXITCODE = 1 }}
    }}
    elseif ($operation.StartsWith('compose exec -T n8n n8n import:workflow ')) {{
        $global:ImportCalls++
        $global:ImportedBeforeReady = $global:ProbeCalls -lt {ready_after}
        $global:LASTEXITCODE = {import_exit_code}
    }}
}}
function Start-Sleep {{ param($Seconds) }}
try {{ & '{quoted_setup}' }} catch {{ $global:Failure = $_.Exception.Message }}
$result = [ordered]@{{
    probe_calls = $global:ProbeCalls
    import_calls = $global:ImportCalls
    imported_before_ready = $global:ImportedBeforeReady
    probe_code = $global:ProbeCode
    failure = $global:Failure
    restored_location = (Get-Location).Path -eq $initialLocation
}}
Write-Output ('SETUP_RESULT=' + ($result | ConvertTo-Json -Compress))
"""
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    line = next(line for line in result.stdout.splitlines() if line.startswith("SETUP_RESULT="))
    return json.loads(line.removeprefix("SETUP_RESULT="))


def test_setup_waits_for_server_readiness_before_import(tmp_path: Path) -> None:
    result = run_setup(tmp_path, ready_after=3)
    assert result["failure"] is None
    assert result["probe_calls"] == 3
    assert result["import_calls"] == 1
    assert result["imported_before_ready"] is False
    assert "/healthz/readiness" in result["probe_code"]
    assert result["restored_location"] is True


def test_setup_never_imports_when_readiness_stays_unavailable(tmp_path: Path) -> None:
    result = run_setup(tmp_path, ready_after=1000)
    assert 1 < result["probe_calls"] < 1000
    assert result["import_calls"] == 0
    assert "did not become ready" in result["failure"]
    assert result["restored_location"] is True


def test_setup_reports_import_failure_after_readiness(tmp_path: Path) -> None:
    result = run_setup(tmp_path, ready_after=1, import_exit_code=1)
    assert result["import_calls"] == 1
    assert result["failure"] == "n8n workflow import failed"
    assert result["restored_location"] is True
