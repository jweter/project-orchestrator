"""Execution-runtime boundary for Azathoth Foundry."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os, subprocess
from typing import Mapping, Sequence

@dataclass(frozen=True)
class RuntimeRequest:
    argv: tuple[str,...]
    cwd: Path
    env: Mapping[str,str]|None = None
    timeout_seconds: int|None = None

    def __post_init__(self):
        if not self.argv or any(not isinstance(x,str) or not x for x in self.argv):
            raise ValueError("argv must contain executable and arguments")
        if self.timeout_seconds is not None and self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")

@dataclass(frozen=True)
class RuntimeResult:
    runtime_id: str
    returncode: int
    stdout: str
    stderr: str

class NativeWindowsRuntime:
    """Direct argv execution without a shell; suitable for isolated worktrees."""
    runtime_id="native-windows"

    def execute(self,request:RuntimeRequest)->RuntimeResult:
        env=os.environ.copy()
        if request.env:
            env.update(request.env)
        completed=subprocess.run(
            list(request.argv),cwd=request.cwd,env=env,text=True,capture_output=True,
            check=False,timeout=request.timeout_seconds,
        )
        return RuntimeResult(self.runtime_id,completed.returncode,completed.stdout,completed.stderr)

class LaptopActorRuntime(NativeWindowsRuntime):
    """Machine-local actor runtime with explicit, probeable capabilities."""
    runtime_id="laptop-actor"
    capabilities=frozenset({"windows","local","offline"})

    def attest(self)->frozenset[str]:
        """Return only capabilities proven on this host at selection time."""
        proven=set(self.capabilities)
        import shutil
        if shutil.which("ollama"):
            proven.add("ollama")
        # Unreal is intentionally not inferred from Windows alone. It becomes
        # eligible only when an Unreal executable is actually discoverable.
        if shutil.which("UnrealEditor") or shutil.which("UnrealEditor.exe"):
            proven.add("unreal")
        return frozenset(proven)

    def satisfies(self,requirements:Sequence[str])->bool:
        return set(requirements) <= set(self.attest())

class ExecutionRuntimeRegistry:
    def __init__(self,runtimes:Sequence[object]):
        self._runtimes={}
        for runtime in runtimes:
            runtime_id=getattr(runtime,"runtime_id",None)
            if not isinstance(runtime_id,str) or not runtime_id:
                raise ValueError("runtime_id required")
            if runtime_id in self._runtimes:
                raise ValueError("duplicate runtime_id")
            self._runtimes[runtime_id]=runtime

    def require(self,runtime_id:str):
        try:
            return self._runtimes[runtime_id]
        except KeyError as exc:
            raise RuntimeError(f"EXECUTION_RUNTIME_UNAVAILABLE:{runtime_id}") from exc

@dataclass(frozen=True)
class RuntimeHealth:
    runtime_id: str
    status: str
    running: int = 0
    capacity: int = 1

    @property
    def available(self)->bool:
        return self.status=="HEALTHY" and self.running < self.capacity

def runtime_health(runtime_id:str,running:int=0,capacity:int=1,
                   registry:ExecutionRuntimeRegistry|None=None)->RuntimeHealth:
    registry=registry or DEFAULT_EXECUTION_RUNTIMES
    try:
        runtime=registry.require(runtime_id)
        attest=getattr(runtime,"attest",None)
        if callable(attest):
            attest()
    except Exception:
        return RuntimeHealth(runtime_id,"UNAVAILABLE",running,capacity)
    if running >= capacity:
        return RuntimeHealth(runtime_id,"SATURATED",running,capacity)
    return RuntimeHealth(runtime_id,"HEALTHY",running,capacity)

def require_runtime_capabilities(runtime_id:str,requirements:Sequence[str],
                                 registry:ExecutionRuntimeRegistry|None=None):
    registry=registry or DEFAULT_EXECUTION_RUNTIMES
    runtime=registry.require(runtime_id)
    if requirements:
        satisfies=getattr(runtime,"satisfies",None)
        if not callable(satisfies) or not satisfies(requirements):
            raise RuntimeError(f"EXECUTION_RUNTIME_CAPABILITY_MISMATCH:{runtime_id}")
    return runtime

DEFAULT_EXECUTION_RUNTIMES=ExecutionRuntimeRegistry([
    NativeWindowsRuntime(),
    LaptopActorRuntime(),
])
