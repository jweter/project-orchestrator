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
    products={x.product for x in graph}
    if len(products)>1: raise ValueError("artifact graph must belong to one product")
    known=set(ids)
    for item in graph:
        missing=set(item.depends_on)-known
        if missing: raise ValueError(f"missing dependency: {sorted(missing)}")
        if item.kind is ArtifactKind.RESULT and not item.evidence:
            raise ValueError("RESULT requires evidence")
        if item.kind is ArtifactKind.TASK and not item.acceptance:
            raise ValueError("TASK requires acceptance criteria")
    by_id={x.artifact_id:x for x in graph}
    visiting:set[str]=set(); visited:set[str]=set()
    def visit(node_id:str)->None:
        if node_id in visiting: raise ValueError("cyclic artifact dependency")
        if node_id in visited: return
        visiting.add(node_id)
        for dep in by_id[node_id].depends_on: visit(dep)
        visiting.remove(node_id); visited.add(node_id)
    for node_id in ids: visit(node_id)
    return graph

def ready_tasks(artifacts: Iterable[SpecArtifact], completed: Iterable[str]) -> list[SpecArtifact]:
    graph=validate_graph(artifacts); done=set(completed)
    return [x for x in graph if x.kind is ArtifactKind.TASK and set(x.depends_on)<=done and x.artifact_id not in done]

def impacted_repositories(artifacts: Iterable[SpecArtifact], artifact_id: str) -> tuple[str, ...]:
    graph=validate_graph(artifacts)
    by_id={x.artifact_id:x for x in graph}
    if artifact_id not in by_id: raise KeyError(artifact_id)
    reachable={artifact_id}
    changed=True
    while changed:
        changed=False
        for item in graph:
            if item.artifact_id not in reachable and set(item.depends_on) & reachable:
                reachable.add(item.artifact_id); changed=True
    return tuple(sorted({repo for node_id in reachable for repo in by_id[node_id].repositories}))
