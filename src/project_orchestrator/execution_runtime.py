"""Execution-runtime boundary for Azathoth Foundry."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os, subprocess, tempfile, time
from typing import Mapping, Sequence

from .windows_process_job import (
    CREATE_SUSPENDED, KillOnCloseJob, ProcessJobError, resume_suspended_process,
)

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

    @staticmethod
    def _terminate_tree(process:subprocess.Popen,grace_seconds:float=3.0)->None:
        """Revoke the whole Windows process tree, not merely its root process."""
        if process.poll() is not None: return
        if os.name=="nt":
            killer=subprocess.run(["taskkill","/PID",str(process.pid),"/T","/F"],
                                  text=True,capture_output=True,check=False)
            try: process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
            if killer.returncode not in {0,128}:  # 128: process already exited
                raise RuntimeError("PROCESS_TREE_TERMINATION_FAILED")
            return
        process.terminate()
        try: process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired: process.kill(); process.wait()

    def execute(self,request:RuntimeRequest)->RuntimeResult:
        env=os.environ.copy()
        if request.env:
            env.update(request.env)
        completed=subprocess.run(
            list(request.argv),cwd=request.cwd,env=env,text=True,capture_output=True,
            check=False,timeout=request.timeout_seconds,
        )
        return RuntimeResult(self.runtime_id,completed.returncode,completed.stdout,completed.stderr)

    def execute_supervised(self,request:RuntimeRequest,authority_check,interval:float=1.0)->RuntimeResult:
        """Execute while authority remains valid without blocking on full output pipes."""
        env=os.environ.copy()
        if request.env: env.update(request.env)
        # PIPE + poll is unsafe here: a verbose child can fill the OS pipe buffer
        # and block forever before poll() observes exit. Spool output to disk while
        # the parent remains free to heartbeat/revoke the execution authority.
        with tempfile.TemporaryFile(mode="w+",encoding="utf-8",errors="replace") as stdout_file, \
             tempfile.TemporaryFile(mode="w+",encoding="utf-8",errors="replace") as stderr_file:
            # Attach only THIS newly spawned process to an owned Job Object.
            # Closing it kills descendants even if the immediate parent exits.
            # Never enumerate/terminate other sessions or worker processes.
            job = KillOnCloseJob() if os.name == "nt" else None
            process = None
            try:
                process=subprocess.Popen(
                    list(request.argv),cwd=request.cwd,env=env,text=True,
                    stdout=stdout_file,stderr=stderr_file,
                    creationflags=CREATE_SUSPENDED if os.name == "nt" else 0,
                )
                try:
                    if job is not None:
                        job.attach(process)
                        try:
                            authority_check()
                        except Exception as exc:
                            job.close()
                            if process.poll() is None:
                                process.kill()
                            process.wait()
                            raise RuntimeError("EXECUTION_AUTHORITY_REVOKED") from exc
                        resume_suspended_process(process)
                except ProcessJobError as exc:
                    # A never-resumed process cannot have created descendants.
                    # Kill only our newly created process by its owned handle.
                    if job is not None:
                        job.close()
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    raise RuntimeError("EXECUTION_PROCESS_TREE_FENCE_UNAVAILABLE") from exc
                deadline=None if request.timeout_seconds is None else time.monotonic()+request.timeout_seconds
                while process.poll() is None:
                    try:
                        authority_check()
                    except Exception:
                        if job is not None:
                            job.close()
                        self._terminate_tree(process)
                        raise RuntimeError("EXECUTION_AUTHORITY_REVOKED")
                    if deadline is not None and time.monotonic()>=deadline:
                        if job is not None:
                            job.close()
                        self._terminate_tree(process)
                        assert request.timeout_seconds is not None
                        raise subprocess.TimeoutExpired(request.argv,float(request.timeout_seconds))
                    time.sleep(interval)
                process.wait()
                try:
                    authority_check()
                except Exception as exc:
                    raise RuntimeError("EXECUTION_AUTHORITY_REVOKED") from exc
                stdout_file.seek(0); stderr_file.seek(0)
                return RuntimeResult(
                    self.runtime_id,process.returncode,stdout_file.read(),stderr_file.read()
                )
            finally:
                if job is not None:
                    job.close()
                if process is not None and process.poll() is None:
                    self._terminate_tree(process)

class LaptopActorRuntime(NativeWindowsRuntime):
    """Machine-local actor runtime with explicit, probeable capabilities."""
    runtime_id="laptop-actor"
    capabilities=frozenset({"windows","local","offline"})

    @staticmethod
    def unreal_editor_path()->Path|None:
        """Resolve a concrete UnrealEditor executable from explicit config, PATH, or Epic installs."""
        import shutil
        configured=os.environ.get("UNREAL_EDITOR","").strip()
        if configured:
            candidate=Path(configured)
            if candidate.is_file(): return candidate
        on_path=shutil.which("UnrealEditor") or shutil.which("UnrealEditor.exe")
        if on_path: return Path(on_path)
        roots=[Path(os.environ.get("ProgramFiles",r"C:\Program Files"))/"Epic Games"]
        for root in roots:
            if not root.is_dir(): continue
            candidates=sorted(root.glob("UE_*/Engine/Binaries/Win64/UnrealEditor.exe"),reverse=True)
            if candidates: return candidates[0]
        return None

    def attest(self)->frozenset[str]:
        """Return only capabilities proven on this host at selection time."""
        proven=set(self.capabilities)
        import shutil
        if shutil.which("ollama"):
            proven.add("ollama")
        if self.unreal_editor_path() is not None:
            proven.add("unreal")
        return frozenset(proven)

    def provenance(self,required_capabilities:Sequence[str]|None=None)->dict[str,object]:
        """Concrete machine evidence, hashing optional tools only when the task requires them."""
        caps=sorted(self.attest())
        evidence:dict[str,object]={"runtime_id":self.runtime_id,"capabilities":caps}
        required=set(caps if required_capabilities is None else required_capabilities)
        if "unreal" in caps and "unreal" in required:
            editor=self.unreal_editor_path()
            if editor is not None:
                resolved=editor.resolve()
                evidence["unreal_editor_path"]=str(resolved)
                try:
                    evidence["unreal_editor_sha256"]=__import__("hashlib").sha256(resolved.read_bytes()).hexdigest()
                except OSError:
                    raise RuntimeError("UNREAL_RUNTIME_PROVENANCE_UNREADABLE")
        return evidence

    def satisfies(self,requirements:Sequence[str])->bool:
        return set(requirements) <= set(self.attest())

class ExecutionRuntimeRegistry:
    def __init__(self,runtimes:Sequence[object]):
        self._runtimes: dict[str, object] = {}
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
