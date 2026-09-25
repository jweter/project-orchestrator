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
