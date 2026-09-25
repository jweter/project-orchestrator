import sys
import pytest
from pathlib import Path
from project_orchestrator.execution_runtime import NativeWindowsRuntime,RuntimeRequest

def test_supervised_runtime_heartbeats_while_process_runs(tmp_path):
    checks=[]
    r=NativeWindowsRuntime().execute_supervised(
        RuntimeRequest((sys.executable,"-c","import time; time.sleep(.15); print('ok')"),tmp_path),
        lambda:checks.append(1),interval=.02)
    assert r.returncode==0 and "ok" in r.stdout
    assert len(checks)>=2

def test_supervised_runtime_terminates_when_authority_revoked(tmp_path):
    checks={"n":0}
    def authority():
        checks["n"]+=1
        if checks["n"]>=2: raise RuntimeError("revoked")
    with pytest.raises(RuntimeError,match="EXECUTION_AUTHORITY_REVOKED"):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest((sys.executable,"-c","import time; time.sleep(10)"),tmp_path),
            authority,interval=.02)
    assert checks["n"]>=2
