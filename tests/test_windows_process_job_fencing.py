"""Regression protection: supervised Windows descendants remain in their own job."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from project_orchestrator.execution_runtime import NativeWindowsRuntime, RuntimeRequest
from project_orchestrator.windows_process_job import ProcessJobError


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_normal_parent_exit_closes_job_and_kills_remaining_descendants(tmp_path):
    marker = tmp_path / "escaped-child.txt"
    child = (
        "import pathlib,time;time.sleep(1.0);"
        f"pathlib.Path({str(marker)!r}).write_text('escaped');"
        "time.sleep(6)"
    )
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
        "time.sleep(.25);print('parent exited')"
    )
    result = NativeWindowsRuntime().execute_supervised(
        RuntimeRequest((sys.executable, "-c", parent), tmp_path),
        lambda: None, interval=.05,
    )
    assert result.returncode == 0
    assert "parent exited" in result.stdout
    time.sleep(1.2)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_job_attach_failure_aborts_and_kills_its_own_new_process(monkeypatch, tmp_path):
    from project_orchestrator import execution_runtime

    marker = tmp_path / "unfenced-process.txt"
    code = (
        "import pathlib,time;time.sleep(1.0);"
        f"pathlib.Path({str(marker)!r}).write_text('unfenced')"
    )

    class RefusingJob:
        closed = False

        def attach(self, _process):
            raise ProcessJobError("simulated Windows assignment denial")

        def close(self):
            self.closed = True

    monkeypatch.setattr(execution_runtime, "KillOnCloseJob", RefusingJob)
    with pytest.raises(RuntimeError, match="EXECUTION_PROCESS_TREE_FENCE_UNAVAILABLE"):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest((sys.executable, "-c", code), tmp_path),
            lambda: None, interval=.05,
        )
    time.sleep(1.2)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_authority_denial_before_resume_does_not_run_child(tmp_path):
    marker = tmp_path / "revoked-before-resume.txt"
    code = (
        "import pathlib,time;"
        f"pathlib.Path({str(marker)!r}).write_text('started');"
        "time.sleep(1)"
    )

    def denied():
        raise RuntimeError("revoked")

    with pytest.raises(RuntimeError, match="EXECUTION_AUTHORITY_REVOKED"):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest((sys.executable, "-c", code), tmp_path),
            denied,
            interval=.05,
        )
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
@pytest.mark.parametrize("n", range(5))
def test_repeated_revocation_cannot_leave_child_alive(n, tmp_path):
    marker = tmp_path / f"orphan-{n}.txt"
    child = (
        "import pathlib,time;time.sleep(.7);"
        f"pathlib.Path({str(marker)!r}).write_text('escaped')"
    )
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
        "time.sleep(5)"
    )
    checks = 0

    def authority():
        nonlocal checks
        checks += 1
        if checks >= 4:
            raise RuntimeError("revoked")

    with pytest.raises(RuntimeError, match="EXECUTION_AUTHORITY_REVOKED"):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest((sys.executable, "-c", parent), tmp_path),
            authority, interval=.05,
        )
    time.sleep(.9)
    assert not marker.exists()
