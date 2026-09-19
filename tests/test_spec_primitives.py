import pytest
from project_orchestrator.spec_primitives import ArtifactKind, SpecArtifact, impacted_repositories, ready_tasks, validate_graph

def a(i,k,repos=("core",),deps=(),accept=(),evidence=()):
    return SpecArtifact(i,k,"knowledge-engine",repos,deps,accept,evidence)

def test_rejects_missing_dependency():
    with pytest.raises(ValueError,match="missing dependency"): validate_graph([a("t",ArtifactKind.TASK,deps=("x",),accept=("green",))])

def test_task_requires_acceptance():
    with pytest.raises(ValueError,match="acceptance"): validate_graph([a("t",ArtifactKind.TASK)])

def test_result_requires_evidence():
    with pytest.raises(ValueError,match="evidence"): validate_graph([a("r",ArtifactKind.RESULT)])

def test_ready_tasks_are_dependency_gated():
    g=[a("s",ArtifactKind.SPEC),a("t",ArtifactKind.TASK,deps=("s",),accept=("preflight green",))]
    assert ready_tasks(g,set())==[]
    assert [x.artifact_id for x in ready_tasks(g,{"s"})]==["t"]

def test_cross_repo_graph_exposes_impacted_repositories():
    g=[a("s",ArtifactKind.SPEC,("core",)),a("w",ArtifactKind.TASK,("web",),("s",),("green",)),a("ai",ArtifactKind.TASK,("ai",),("w",),("green",))]
    assert impacted_repositories(g,"s")==("ai","core","web")
