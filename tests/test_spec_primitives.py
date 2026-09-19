import pytest
from project_orchestrator.spec_primitives import ArtifactKind, SpecArtifact, impacted_repositories, ready_tasks, validate_graph

def a(i,k,repos=("core",),deps=(),accept=(),evidence=(),product="knowledge-engine"):
    return SpecArtifact(i,k,product,repos,deps,accept,evidence)

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

def test_cross_repo_graph_exposes_only_declared_dependency_impact():
    g=[a("a",ArtifactKind.SPEC,("core",)),a("b",ArtifactKind.SPEC,("core",)),a("w",ArtifactKind.TASK,("web",),("b",),("green",)),a("ai",ArtifactKind.TASK,("ai",),("a",),("green",))]
    assert impacted_repositories(g,"a")==("ai","core")
    assert impacted_repositories(g,"b")==("core","web")

def test_cycle_is_rejected():
    g=[a("a",ArtifactKind.TASK,deps=("b",),accept=("green",)),a("b",ArtifactKind.TASK,deps=("a",),accept=("green",))]
    with pytest.raises(ValueError,match="cyclic"): validate_graph(g)

def test_self_cycle_is_rejected():
    with pytest.raises(ValueError,match="cyclic"): validate_graph([a("a",ArtifactKind.TASK,deps=("a",),accept=("green",))])

def test_mixed_product_graph_is_rejected():
    with pytest.raises(ValueError,match="one product"): validate_graph([a("a",ArtifactKind.SPEC),a("b",ArtifactKind.SPEC,product="rocksmith")])
