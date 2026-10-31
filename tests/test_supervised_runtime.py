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

def test_supervised_runtime_drains_large_stdout_and_stderr_without_deadlock(tmp_path):
    script=(
        "import sys;"
        "sys.stdout.write('x'*2_000_000);"
        "sys.stderr.write('y'*1_000_000)"
    )
    checks=[]
    r=NativeWindowsRuntime().execute_supervised(
        RuntimeRequest((sys.executable,"-c",script),tmp_path,timeout_seconds=10),
        lambda:checks.append(1),interval=.01)
    assert r.returncode==0
    assert len(r.stdout)==2_000_000
    assert len(r.stderr)==1_000_000
    assert checks


# Unit-level #570 parity (originally written by the Copilot agent on #586, 9fa69ab; adapted so a revoked
# launch must go through the process-tree terminator, never a bare kill of the root).

def _fake_windows_launch(monkeypatch, process, terminated):
    import os
    from types import SimpleNamespace

    import project_orchestrator.execution_runtime as runtime

    class Job:
        def attach(self, _process):
            process.attached = True

        def close(self):
            process.job_closed = True

    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(runtime, "KillOnCloseJob", Job)
    monkeypatch.setattr(NativeWindowsRuntime, "_terminate_tree",
                        staticmethod(lambda proc, grace_seconds=3.0: (terminated.append(proc), setattr(proc, "returncode", 1))))
    return runtime


def test_supervised_runtime_checks_authority_before_resuming(monkeypatch, tmp_path):
    class Process:
        returncode = None
        attached = job_closed = False

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    process, terminated, resumed = Process(), [], []
    runtime = _fake_windows_launch(monkeypatch, process, terminated)
    monkeypatch.setattr(runtime, "resume_suspended_process", lambda _process: resumed.append(True))

    def denied():
        raise RuntimeError("revoked")

    with pytest.raises(RuntimeError, match="EXECUTION_AUTHORITY_REVOKED"):
        NativeWindowsRuntime().execute_supervised(RuntimeRequest((sys.executable, "-c", "pass"), tmp_path), denied)
    assert process.attached and not resumed  # never resumed: the child never ran
    assert terminated == [process] and process.job_closed  # the tree terminator and the job both fired


def test_supervised_runtime_rechecks_authority_after_process_completion(monkeypatch, tmp_path):
    class Process:
        returncode = 0
        attached = job_closed = finished = False
        polls = 0

        def poll(self):
            self.polls += 1
            return None if self.polls == 1 else 0

        def wait(self, timeout=None):
            self.finished = True
            return self.returncode

    process, terminated = Process(), []
    runtime = _fake_windows_launch(monkeypatch, process, terminated)
    monkeypatch.setattr(runtime, "resume_suspended_process", lambda _process: None)

    def authority():
        if process.finished:
            raise RuntimeError("revoked after completion")

    with pytest.raises(RuntimeError, match="EXECUTION_AUTHORITY_REVOKED"):
        NativeWindowsRuntime().execute_supervised(RuntimeRequest((sys.executable, "-c", "pass"), tmp_path),
                                                  authority, interval=0)
    assert process.job_closed  # job-owned descendants are killed with the job
