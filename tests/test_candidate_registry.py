import pytest

from project_orchestrator.candidate_registry import (
    CandidateRegistryError,
    attach_verified_learning,
    refresh_registry,
    registry_snapshot,
    select_release_frontier,
    validate_registry,
)


def candidate(**overrides):
    item = {
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-ai",
        "source_type": "decomposition",
        "source_ref": "knowledge-engine-ai#129/report-parser",
        "source_revision": "rev-a",
        "source_state": "OPEN",
        "priority": "P5",
        "paths": ["src/project_orchestrator/report_parser.py"],
        "dependencies": [],
        "task": "strengthen report parser",
        "conflict_free": True,
    }
    item.update(overrides)
    return item


def test_refresh_is_idempotent_for_same_authoritative_snapshot():
    empty = {"schema_version": 1, "entries": []}
    first = refresh_registry(empty, [candidate()])
    second = refresh_registry(first, [candidate()])
    assert second == first


def test_source_revision_change_invalidates_head_sensitive_evidence():
    first = refresh_registry({"schema_version": 1, "entries": []}, [candidate()])
    second = refresh_registry(first, [candidate(source_revision="rev-b")])
    item = second["entries"][0]
    assert item["previous_source_revision"] == "rev-a"
    assert item["head_sensitive_evidence_valid"] is False


def test_removed_source_is_retained_but_cannot_be_ready():
    first = refresh_registry({"schema_version": 1, "entries": []}, [candidate()])
    removed = refresh_registry(first, [])
    snap = registry_snapshot(removed)
    assert snap["candidates"][0]["source_state"] == "REMOVED"
    assert snap["candidates"][0]["registry_ready"] is False


def test_ambiguous_issue_without_paths_is_not_dispatchable():
    reg = refresh_registry({"schema_version": 1, "entries": []}, [candidate(source_type="issue", paths=[])])
    item = registry_snapshot(reg)["candidates"][0]
    assert item["dispatchable_scope"] is False
    assert item["registry_ready"] is False


def test_dependency_requires_terminal_authoritative_source():
    parent = candidate(source_ref="issue:parent", source_revision="1", source_state="OPEN")
    parent_id = refresh_registry({"schema_version": 1, "entries": []}, [parent])["entries"][0]["candidate_id"]
    child = candidate(source_ref="issue:child", source_revision="1", dependencies=[parent_id])
    reg = refresh_registry({"schema_version": 1, "entries": []}, [parent, child])
    child_snap = next(x for x in registry_snapshot(reg)["candidates"] if x["source_ref"] == "issue:child")
    assert child_snap["dependencies_clear"] is False
    reg = refresh_registry(reg, [candidate(source_ref="issue:parent", source_revision="2", source_state="CLOSED"), child])
    child_snap = next(x for x in registry_snapshot(reg)["candidates"] if x["source_ref"] == "issue:child")
    assert child_snap["dependencies_clear"] is True


def test_active_ownership_and_product_reality_are_preserved_for_dispatch_gate():
    reg = refresh_registry({"schema_version": 1, "entries": []}, [candidate(actively_owned=True, product_reality_required=True)])
    item = registry_snapshot(reg)["candidates"][0]
    assert item["actively_owned"] is True
    assert item["product_reality_required"] is True


def test_learning_reuse_fails_closed_without_capability_index():
    memory = {"schema_version": 1, "entries": []}
    attached = attach_verified_learning(candidate(paths=["report_parser.py"]), memory)
    assert attached["learning_gate"] == "CAPABILITY_INDEX_REQUIRED"
    assert attached["capabilities_to_reuse"] == []
    assert attached["capability_reuse_refs"] == []
    assert attached["product_reality_inferred"] is False


def test_duplicate_authoritative_source_fails_closed():
    with pytest.raises(CandidateRegistryError):
        refresh_registry({"schema_version": 1, "entries": []}, [candidate(), candidate()])


def test_release_frontier_prefers_verified_gate_within_same_priority():
    ordinary = candidate(source_ref="issue:ordinary")
    release = candidate(
        source_ref="issue:release", usability_gate_ref="FIRST_WORKING_BUILD:ask",
        usability_gate_source_revision="rev-a", usability_gate_verified=True,
    )
    reg = refresh_registry({"schema_version": 1, "entries": []}, [ordinary, release])
    assert [x["source_ref"] for x in select_release_frontier(reg)] == ["issue:release", "issue:ordinary"]


def test_release_frontier_does_not_override_priority_or_use_stale_evidence():
    urgent = candidate(source_ref="issue:urgent", priority="P1")
    stale = candidate(
        source_ref="issue:stale", usability_gate_ref="FIRST_WORKING_BUILD:ask",
        usability_gate_source_revision="old-head", usability_gate_verified=True,
    )
    current = candidate(
        source_ref="issue:current", usability_gate_ref="FIRST_WORKING_BUILD:ask",
        usability_gate_source_revision="rev-a", usability_gate_verified=True,
    )
    reg = refresh_registry({"schema_version": 1, "entries": []}, [urgent, stale, current])
    ordered = select_release_frontier(reg)
    assert ordered[0]["source_ref"] == "issue:urgent"
    assert ordered[1]["source_ref"] == "issue:current"


def test_release_frontier_excludes_owned_and_dependency_blocked_candidates():
    owned = candidate(source_ref="issue:owned", actively_owned=True)
    blocked = candidate(source_ref="issue:blocked", dependencies=["not-closed"])
    reg = refresh_registry({"schema_version": 1, "entries": []}, [owned, blocked])
    assert select_release_frontier(reg) == []


def test_registry_rejects_unknown_project():
    with pytest.raises(CandidateRegistryError):
        validate_registry({"schema_version": 1, "entries": [candidate(project="orchestrator")]})
