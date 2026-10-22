import os,subprocess,sys,time
import pytest
from project_orchestrator.execution_runtime import NativeWindowsRuntime,RuntimeRequest

@pytest.mark.skipif(os.name!="nt",reason="Windows process-tree contract")
def test_authority_revocation_kills_descendant_tree(tmp_path):
    marker=tmp_path/"child-alive.txt"
    child=("import time,pathlib; time.sleep(1); "
           f"pathlib.Path(r'{marker}').write_text('survived'); time.sleep(20)")
    parent=("import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(20)")
    checks={"n":0}
    def authority():
        checks["n"]+=1
        if checks["n"]>=3: raise RuntimeError("revoked")
    with pytest.raises(RuntimeError,match="EXECUTION_AUTHORITY_REVOKED"):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest((sys.executable,"-c",parent),tmp_path),authority,interval=.05)
    time.sleep(1.3)
    assert not marker.exists()

def test_supervised_runtime_routes_revocation_through_tree_terminator(monkeypatch,tmp_path):
    runtime=NativeWindowsRuntime(); seen=[]
    original=runtime._terminate_tree
    monkeypatch.setattr(runtime,"_terminate_tree",lambda process,grace_seconds=3.0:(seen.append(process.pid),original(process,grace_seconds))[1])
    with pytest.raises(RuntimeError,match="EXECUTION_AUTHORITY_REVOKED"):
        runtime.execute_supervised(RuntimeRequest((sys.executable,"-c","import time; time.sleep(5)"),tmp_path),
                                   lambda:(_ for _ in ()).throw(RuntimeError("revoked")),interval=.01)
    assert len(seen)==1


@pytest.mark.skipif(os.name!="nt",reason="Windows Job Object contract")
def test_revocation_before_child_resume_runs_no_child_code(tmp_path):
    marker=tmp_path/"unauthorized-execution.txt"
    script=f"import pathlib; pathlib.Path(r'{marker}').write_text('unauthorized')"
    with pytest.raises(RuntimeError,match="EXECUTION_AUTHORITY_REVOKED"):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest((sys.executable,"-c",script),tmp_path),
            lambda: (_ for _ in ()).throw(RuntimeError("owner veto")),
            interval=.01,
        )
    time.sleep(.3)
    assert not marker.exists()


@pytest.mark.skipif(os.name!="nt",reason="Windows Job Object contract")
def test_root_exit_still_terminates_job_owned_descendant(tmp_path):
    marker=tmp_path/"orphan-after-root-exit.txt"
    child=("import time,pathlib; time.sleep(1); "
           f"pathlib.Path(r'{marker}').write_text('orphan')")
    parent=("import subprocess,sys; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}])")
    result=NativeWindowsRuntime().execute_supervised(
        RuntimeRequest((sys.executable,"-c",parent),tmp_path),
        lambda: None,interval=.02,
    )
    assert result.returncode==0
    time.sleep(1.3)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_failed_containment_never_runs_unowned_child(tmp_path, monkeypatch):
    from project_orchestrator.windows_process_job import WindowsProcessJob

    marker = tmp_path / "uncontained-child-executed.txt"

    def reject_attach(self, process):
        raise RuntimeError("PROCESS_TREE_CONTAINMENT_UNAVAILABLE")

    monkeypatch.setattr(WindowsProcessJob, "attach_suspended", reject_attach)
    with pytest.raises(RuntimeError, match="PROCESS_TREE_CONTAINMENT_UNAVAILABLE"):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest(
                (sys.executable, "-c", f"from pathlib import Path; Path(r'{marker}').write_text('BAD')"),
                tmp_path,
            ),
            lambda: None,
            interval=.01,
        )
    time.sleep(.3)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_authority_rechecked_after_fast_root_exits(tmp_path):
    finished_marker = tmp_path / "root-finished.txt"

    def authority():
        if finished_marker.exists():
            raise RuntimeError("revoked after completion")

    with pytest.raises(RuntimeError, match="EXECUTION_AUTHORITY_REVOKED"):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest(
                (sys.executable, "-c", f"from pathlib import Path; Path(r'{finished_marker}').write_text('done')"),
                tmp_path,
            ),
            authority,
            interval=.02,
        )
    assert finished_marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_timeout_terminates_job_descendant(tmp_path):
    marker = tmp_path / "descendant-after-timeout.txt"
    child = ("import time,pathlib; time.sleep(2); "
             f"pathlib.Path(r'{marker}').write_text('survived')")
    parent = ("import subprocess,sys,time; "
              f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(10)")
    with pytest.raises(subprocess.TimeoutExpired):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest((sys.executable, "-c", parent), tmp_path, timeout_seconds=1),
            lambda: None,
            interval=.02,
        )
    time.sleep(2.3)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_failed_primary_thread_resume_never_reports_success(tmp_path, monkeypatch):
    from project_orchestrator.windows_process_job import WindowsProcessJob

    marker = tmp_path / "resume-failed-child-executed.txt"
    monkeypatch.setattr(WindowsProcessJob, "_resume_primary_thread", lambda self, pid: False)
    with pytest.raises(RuntimeError, match="PROCESS_TREE_RESUME_FAILED"):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest(
                (sys.executable, "-c", f"from pathlib import Path; Path(r'{marker}').write_text('BAD')"),
                tmp_path,
            ),
            lambda: None,
            interval=.01,
        )
    time.sleep(.3)
    assert not marker.exists()
