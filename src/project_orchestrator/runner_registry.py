"""Deterministic runner registry for Azathoth Foundry task dispatch."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Mapping
import shutil, subprocess

@dataclass(frozen=True)
class RunnerManifest:
    runner_id: str
    capabilities: frozenset[str]
    max_concurrency: int = 1
    priority: int = 100
    enabled: bool = True

    def __post_init__(self):
        if not self.runner_id.strip():
            raise ValueError("runner_id required")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if not self.capabilities:
            raise ValueError("capabilities required")

class RunnerRegistry:
    """Select runners by explicit task requirements; never infer hidden capability."""

    def __init__(self, manifests: Iterable[RunnerManifest]):
        items=list(manifests)
        ids=[m.runner_id for m in items]
        if len(ids)!=len(set(ids)):
            raise ValueError("duplicate runner_id")
        self._manifests={m.runner_id:m for m in items}

    def eligible(self, requirements: Iterable[str], running: Mapping[str,int]|None=None)->list[RunnerManifest]:
        required=frozenset(requirements)
        if not required:
            raise ValueError("task runner requirements required")
        running=running or {}
        return sorted(
            (m for m in self._manifests.values()
             if m.enabled and required <= m.capabilities
             and running.get(m.runner_id,0) < m.max_concurrency),
            key=lambda m:(m.priority,m.runner_id),
        )

    def select(self, requirements: Iterable[str], running: Mapping[str,int]|None=None)->RunnerManifest:
        candidates=self.eligible(requirements,running)
        if not candidates:
            raise RuntimeError("NO_ELIGIBLE_RUNNER")
        return candidates[0]

def claude_code_healthy(timeout_seconds:int=30)->bool:
    """Require both an installed CLI and a successful authenticated headless probe."""
    if not shutil.which("claude"):
        return False
    try:
        probe=subprocess.run(
            ["claude","-p","--output-format","text","Reply with exactly FOUNDRY_RUNNER_READY"],
            text=True,capture_output=True,timeout=timeout_seconds,check=False,
        )
    except (OSError,subprocess.TimeoutExpired):
        return False
    return probe.returncode==0 and probe.stdout.strip()=="FOUNDRY_RUNNER_READY"

def default_runner_registry(*,probe_external:bool=True)->RunnerRegistry:
    claude_enabled=claude_code_healthy() if probe_external else False
    return RunnerRegistry([
        RunnerManifest("aider-ollama",frozenset({"code","local","windows","offline"}),1,30),
        RunnerManifest("codex",frozenset({"code","review"}),2,10,False),
        RunnerManifest("claude-code",frozenset({"code","review","local","windows","offline"}),2,20,claude_enabled),
    ])

DEFAULT_RUNNERS=default_runner_registry()
