"""Native spec/change primitives distilled from external SDD systems."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

class ArtifactKind(str, Enum):
    GOAL="GOAL"; SPEC="SPEC"; DESIGN="DESIGN"; TASK="TASK"; EVIDENCE="EVIDENCE"; RESULT="RESULT"

@dataclass(frozen=True)
class SpecArtifact:
    artifact_id: str
    kind: ArtifactKind
    product: str
    repositories: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

def validate_graph(artifacts: Iterable[SpecArtifact]) -> tuple[SpecArtifact, ...]:
    graph=tuple(artifacts)
    ids=[x.artifact_id for x in graph]
    if len(ids)!=len(set(ids)): raise ValueError("duplicate artifact id")
    known=set(ids)
    for item in graph:
        missing=set(item.depends_on)-known
        if missing: raise ValueError(f"missing dependency: {sorted(missing)}")
        if item.kind is ArtifactKind.RESULT and not item.evidence:
            raise ValueError("RESULT requires evidence")
        if item.kind is ArtifactKind.TASK and not item.acceptance:
            raise ValueError("TASK requires acceptance criteria")
    return graph

def ready_tasks(artifacts: Iterable[SpecArtifact], completed: Iterable[str]) -> list[SpecArtifact]:
    graph=validate_graph(artifacts); done=set(completed)
    return [x for x in graph if x.kind is ArtifactKind.TASK and set(x.depends_on)<=done and x.artifact_id not in done]

def impacted_repositories(artifacts: Iterable[SpecArtifact], artifact_id: str) -> tuple[str, ...]:
    graph=validate_graph(artifacts)
    by_id={x.artifact_id:x for x in graph}
    if artifact_id not in by_id: raise KeyError(artifact_id)
    impacted=set(by_id[artifact_id].repositories)
    changed=True
    while changed:
        changed=False
        for item in graph:
            if set(item.depends_on) & {x.artifact_id for x in graph if set(x.repositories) & impacted}:
                before=len(impacted); impacted.update(item.repositories); changed |= len(impacted)>before
    return tuple(sorted(impacted))
