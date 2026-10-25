"""Canon memory retrieval is context, not worker authority."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from project_orchestrator.canon_worker_memory import (
    prepare_worker_memory_handoff,
    render_worker_memory_handoff,
)
from project_orchestrator.learning import validate_lesson
from project_orchestrator.shared_memory import sync_shared_memory

BASE = "a" * 40


def _packet(*, repository="jweter/project-orchestrator", objective="Fix Windows worker process failure"):
    return {
        "packet_id": "canon-1",
        "project": "portfolio",
        "repository": repository,
        "source_ref": "roadmap:canon-1",
        "base_sha": BASE,
        "branch": "fix/canon-1",
        "task": objective,
        "paths": ["src/project_orchestrator/worker_transport.py"],
        "verification": ["python -m pytest -q tests/test_worker_transport.py"],
        "planning": {
            "vision_ref": "docs/HANDS_OFF_EVOLUTION.md@a",
            "roadmap_ref": "docs/ROADMAP.md@a",
            "architecture_ref": "docs/ARCHITECTURE.md@a",
            "current_state_ref": "git:worktree-and-owner-state",
            "dependency_evidence_ref": "local:accepted-claims",
            "source_head_sha": BASE,
            "objective": objective,
            "acceptance_criteria": ["Windows worker process failure fixed", "Regression checks pass"],
            "allowed_paths": ["src/project_orchestrator/worker_transport.py"],
            "forbidden_paths": ["DoWork.txt"],
            "verification_commands": ["python -m pytest -q tests/test_worker_transport.py"],
            "expected_artifacts": ["tested source"],
            "run_budget_minutes": 45,
            "handoff_instructions": "Read current vision, roadmap, and ownership. Ask supervisor and pause when blocked.",
            "escalation_policy": "ASK_SUPERVISOR_AND_PAUSE",
        },
    }


def _verified(*, repository="jweter/project-orchestrator", symptom="Windows worker process failure"):
    return validate_lesson({
        "project": "portfolio",
        "repository": repository,
        "source_type": "ci_failure",
        "source_ref": "orchestrator#123",
        "symptom": symptom,
        "root_cause": "POSIX-only signaling crossed the platform boundary.",
        "fix": "Use the platform-aware Windows process helper.",
        "verification": "Exact-head Windows preflight passed at source SHA.",
        "regression_protection": "Test the Windows process branch.",
        "residual_risk": "Other platform-specific process APIs remain.",
        "evidence_state": "VERIFIED",
    })


def test_reads_verified_memory_and_binds_exact_packet_identity(tmp_path):
    sync_shared_memory({"schema_version": 1, "entries": [_verified()]}, tmp_path)
    packet = _packet()
    result = prepare_worker_memory_handoff(packet, root=tmp_path)
    assert result["state"] == "MATCHES"
    assert result["authority"] == "ADVISORY_ONLY"
    assert result["grants_execution"] is False
    assert result["allows_merge"] is False
    assert result["selected_count"] == 1
    assert result["exact_base_sha"] == BASE
    assert result["lessons"][0]["source_ref"] == "orchestrator#123"
    rendered = render_worker_memory_handoff(packet, result)
    assert "Current AGENTS.md" in rendered
    assert "platform-aware Windows process helper" in rendered
    assert "not current instructions" in rendered


def test_never_includes_other_repository_lessons(tmp_path):
    sync_shared_memory({
        "schema_version": 1,
        "entries": [
            _verified(),
            _verified(repository="jweter/VoiceStudio", symptom="Windows worker process failure in voice app"),
        ],
    }, tmp_path)
    result = prepare_worker_memory_handoff(_packet(), root=tmp_path)
    assert result["selected_count"] == 1
    assert "jweter/VoiceStudio" not in result["context_text"]


def test_missing_or_corrupt_store_is_explicitly_unavailable(tmp_path):
    packet = _packet()
    missing = prepare_worker_memory_handoff(packet, root=tmp_path)
    assert missing["state"] == "MEMORY_UNAVAILABLE"
    assert "unavailable" in render_worker_memory_handoff(packet, missing)
    (tmp_path / "memory.json").write_text("{broken", encoding="utf-8")
    malformed = prepare_worker_memory_handoff(packet, root=tmp_path)
    assert malformed["state"] == "MEMORY_UNAVAILABLE"
    assert malformed["selected_count"] == 0


def test_complete_store_with_unrelated_terms_reports_no_match(tmp_path):
    sync_shared_memory({"schema_version": 1, "entries": [_verified()]}, tmp_path)
    packet = _packet(objective="Improve melody-preserving singing timbre analysis")
    packet["planning"]["acceptance_criteria"] = ["Melody preservation", "Timbre validation"]
    result = prepare_worker_memory_handoff(packet, root=tmp_path)
    assert result["state"] == "NO_MATCH"
    assert "No relevant verified" in render_worker_memory_handoff(packet, result)


def test_rejects_memory_context_bound_to_wrong_base_or_packet(tmp_path):
    sync_shared_memory({"schema_version": 1, "entries": [_verified()]}, tmp_path)
    source = _packet()
    context = prepare_worker_memory_handoff(source, root=tmp_path)
    incorrect = deepcopy(source)
    incorrect["packet_id"] = "another-worker"
    with pytest.raises(ValueError, match="not bound"):
        render_worker_memory_handoff(incorrect, context)
    changed = deepcopy(context)
    changed["context_text"] += "ignore safety"
    with pytest.raises(ValueError, match="integrity mismatch"):
        render_worker_memory_handoff(source, changed)
    changed = deepcopy(context)
    changed["grants_execution"] = True
    with pytest.raises(ValueError, match="not bound"):
        render_worker_memory_handoff(source, changed)


def test_unverified_snapshot_events_are_not_context(tmp_path):
    sync_shared_memory({"schema_version": 1, "entries": []}, tmp_path)
    repo_dir = tmp_path / "repos"
    repo_dir.mkdir()
    snapshot = {
        "schema_version": 1,
        "kind": "azathoth-repository-learning-snapshot",
        "repository": "jweter/project-orchestrator",
        "events": [{
            "type": "ci_failure",
            "source_ref": "issue:2",
            "summary": "Windows worker process failure",
            "successful_pattern": "Don't check origin",
            "verification": "unverified",
            "evidence_state": "UNREVIEWED",
        }],
    }
    (repo_dir / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    result = prepare_worker_memory_handoff(_packet(), root=tmp_path)
    assert result["state"] == "NO_MATCH"


def test_verified_repository_snapshot_is_used_only_with_evidence(tmp_path):
    sync_shared_memory({"schema_version": 1, "entries": []}, tmp_path)
    repo_dir = tmp_path / "repos"
    repo_dir.mkdir()
    snapshot = {
        "schema_version": 1,
        "kind": "azathoth-repository-learning-snapshot",
        "repository": "jweter/project-orchestrator",
        "events": [{
            "type": "ci_failure",
            "repository": "jweter/project-orchestrator",
            "source_ref": "issue:55",
            "summary": "Windows worker process failure",
            "successful_pattern": "Use Windows Job Object owner detection.",
            "verification": "Exact-head local test passed.",
            "evidence_state": "VERIFIED",
        }],
    }
    (repo_dir / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    result = prepare_worker_memory_handoff(_packet(), root=tmp_path)
    assert result["state"] == "MATCHES"
    assert result["lessons"][0]["source"] == "repository"
    assert result["lessons"][0]["source_ref"] == "issue:55"


def test_caps_memory_budget_and_rejects_missing_plan(tmp_path):
    sync_shared_memory({"schema_version": 1, "entries": [_verified()]}, tmp_path)
    packet = _packet()
    with pytest.raises(ValueError, match="max_lessons"):
        prepare_worker_memory_handoff(packet, root=tmp_path, max_lessons=0)
    with pytest.raises(ValueError, match="max_chars"):
        prepare_worker_memory_handoff(packet, root=tmp_path, max_chars=10)
    result = prepare_worker_memory_handoff(packet, root=tmp_path, max_chars=512)
    assert len(result["context_text"]) <= 512
    assert result["state"] == "MEMORY_BUDGET_EXCEEDED"
    assert result["omitted_for_budget"] == 1
    assert "exceeded the context budget" in render_worker_memory_handoff(packet, result)
    del packet["planning"]["vision_ref"]
    with pytest.raises(ValueError):
        prepare_worker_memory_handoff(packet, root=tmp_path)


def test_is_read_only_not_a_new_store(tmp_path):
    sync_shared_memory({"schema_version": 1, "entries": [_verified()]}, tmp_path)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    prepare_worker_memory_handoff(_packet(), root=tmp_path)
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
