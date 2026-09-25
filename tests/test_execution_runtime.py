from pathlib import Path
import sys
import pytest
from project_orchestrator.execution_runtime import (
    RuntimeRequest, NativeWindowsRuntime, ExecutionRuntimeRegistry
)

def test_native_runtime_executes_argv_and_captures_evidence(tmp_path):
    r=NativeWindowsRuntime().execute(RuntimeRequest(
        (sys.executable,"-c","import os; print(os.getcwd()); print(os.environ['FOUNDRY_TEST'])"),
        tmp_path,{"FOUNDRY_TEST":"green"},10))
    assert r.runtime_id=="native-windows"
    assert r.returncode==0
    assert str(tmp_path).lower() in r.stdout.lower()
    assert "green" in r.stdout

def test_native_runtime_preserves_nonzero_exit_as_evidence(tmp_path):
    r=NativeWindowsRuntime().execute(RuntimeRequest(
        (sys.executable,"-c","import sys; print('bad',file=sys.stderr); sys.exit(7)"),tmp_path))
    assert r.returncode==7
    assert "bad" in r.stderr

def test_request_rejects_empty_argv_and_invalid_timeout(tmp_path):
    with pytest.raises(ValueError):
        RuntimeRequest(tuple(),tmp_path)
    with pytest.raises(ValueError):
        RuntimeRequest(("python",),tmp_path,timeout_seconds=0)

def test_registry_fails_closed_for_unknown_runtime():
    reg=ExecutionRuntimeRegistry([NativeWindowsRuntime()])
    assert reg.require("native-windows").runtime_id=="native-windows"
    with pytest.raises(RuntimeError,match="EXECUTION_RUNTIME_UNAVAILABLE"):
        reg.require("unreal-workstation")

def test_registry_rejects_duplicate_runtime_ids():
    with pytest.raises(ValueError,match="duplicate"):
        ExecutionRuntimeRegistry([NativeWindowsRuntime(),NativeWindowsRuntime()])

def test_builder_run_uses_runtime_boundary(tmp_path,monkeypatch):
    import scripts.autonomous_slice_builder as builder
    calls=[]
    class Fake:
        def execute(self,request):
            calls.append(request)
            from project_orchestrator.execution_runtime import RuntimeResult
            return RuntimeResult("fake",3,"out","err")
    monkeypatch.setattr(builder.DEFAULT_EXECUTION_RUNTIMES,"require",lambda runtime_id:Fake())
    result=builder.run(["tool","arg"],tmp_path,env={"X":"1"},timeout=9)
    assert result.returncode==3 and result.stdout=="out" and result.stderr=="err"
    assert calls[0].argv==("tool","arg")
    assert calls[0].timeout_seconds==9

def test_laptop_actor_runtime_is_registered_and_preserves_identity(tmp_path):
    from project_orchestrator.execution_runtime import DEFAULT_EXECUTION_RUNTIMES
    runtime=DEFAULT_EXECUTION_RUNTIMES.require("laptop-actor")
    result=runtime.execute(RuntimeRequest((sys.executable,"-c","print('actor')"),tmp_path))
    assert result.runtime_id=="laptop-actor"
    assert result.returncode==0 and "actor" in result.stdout

def test_runtime_routing_separates_machine_specific_tasks():
    import scripts.continuous_flow_supervisor as supervisor
    assert supervisor.runtime_for_task({})=="native-windows"
    assert supervisor.runtime_for_task({"runtime_requirements":["unreal"]})=="laptop-actor"
    assert supervisor.runtime_for_task({"runtime_requirements":["ollama"]})=="laptop-actor"

def test_invalid_runtime_requirements_fail_closed():
    import scripts.continuous_flow_supervisor as supervisor
    with pytest.raises(RuntimeError,match="INVALID_RUNTIME_REQUIREMENTS"):
        supervisor.runtime_for_task({"runtime_requirements":"unreal"})
