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

DEFAULT_EXECUTION_RUNTIMES=ExecutionRuntimeRegistry([NativeWindowsRuntime()])
