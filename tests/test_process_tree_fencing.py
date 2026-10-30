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


@pytest.mark.skipif(os.name!="nt", reason="Windows job-object containment contract")
def test_completed_parent_does_not_leave_detached_descendants(tmp_path):
    marker=tmp_path/"orphan-survived.txt"
    child=("import time,pathlib; time.sleep(.8); "
           f"pathlib.Path(r'{marker}').write_text('orphan')")
    parent=("import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(.3)")
    result=NativeWindowsRuntime().execute_supervised(
        RuntimeRequest((sys.executable,"-c",parent),tmp_path),
        lambda: None,interval=.02,
    )
    assert result.returncode==0
    time.sleep(1)
    assert not marker.exists()


@pytest.mark.skipif(os.name!="nt", reason="Windows job-object containment contract")
def test_supervised_worker_refuses_unavailable_job_authority(monkeypatch,tmp_path):
    import project_orchestrator.execution_runtime as runtime
    from project_orchestrator.windows_process_job import ProcessJobError
    def refuse(self,_process):
        raise ProcessJobError("synthetic job-assignment failure")
    monkeypatch.setattr(runtime.KillOnCloseJob,"attach",refuse)
    with pytest.raises(RuntimeError,match="EXECUTION_PROCESS_TREE_FENCE_UNAVAILABLE"):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest((sys.executable,"-c","import time;time.sleep(10)"),tmp_path),
            lambda: None,interval=.01,
        )


@pytest.mark.skipif(os.name!="nt", reason="Windows suspended-launch contract")
def test_authority_revoked_before_resume_never_runs_child_code(monkeypatch,tmp_path):
    """#570 parity: authority is re-checked while the attached child is still suspended."""
    import project_orchestrator.execution_runtime as runtime
    marker=tmp_path/"child-ran.txt"
    resumed=[]
    original=runtime.resume_suspended_process
    monkeypatch.setattr(runtime,"resume_suspended_process",lambda p:(resumed.append(p.pid),original(p))[1])
    def revoked():
        raise RuntimeError("owner revoked during launch")
    with pytest.raises(RuntimeError,match="EXECUTION_AUTHORITY_REVOKED"):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest((sys.executable,"-c",f"import pathlib; pathlib.Path(r'{marker}').write_text('ran')"),tmp_path),
            revoked,interval=.01,
        )
    assert resumed==[]
    time.sleep(.5)
    assert not marker.exists()


def test_authority_revoked_while_fast_root_exits_returns_no_result(tmp_path):
    """#570 parity: a root that exits between polls cannot report success after revocation."""
    marker=tmp_path/"root-finished.txt"
    def authority():
        if marker.exists():
            raise RuntimeError("owner revoked")
    with pytest.raises(RuntimeError,match="EXECUTION_AUTHORITY_REVOKED"):
        NativeWindowsRuntime().execute_supervised(
            RuntimeRequest((sys.executable,"-c",f"import pathlib; pathlib.Path(r'{marker}').write_text('done')"),tmp_path),
            authority,interval=2.0,
        )
    assert marker.exists()
