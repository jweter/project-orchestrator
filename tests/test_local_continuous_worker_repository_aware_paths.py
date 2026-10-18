"""Proves the real, in-place local_continuous_worker.py fix (2026-10-18,
owner directive 1): runnable_handoffs() and the repair-task builder
(queued_repair_handoffs()) now use the same trusted, repository-aware
REPOSITORY_SOURCE_ROOTS mapping already implemented in
accepted_event_dispatch_coordinator.py, instead of hard-coding "src/".

Loads the real, edited controller directly from its real, on-disk,
shared IronGodLocal path via importlib -- exercising the actual
deployed fix, not a disconnected copy. Every other proof test this
session uses this exact pattern for the same reason.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

CONTROLLER_PATH = Path(
    r"C:\Users\jerem\AppData\Local\ExponentialEngine\IronGodLocal\local_continuous_worker.py"
)

if str(CONTROLLER_PATH.parent) not in sys.path:
    sys.path.insert(0, str(CONTROLLER_PATH.parent))

DEFAULT_REPOSITORY = "test-proof-owner/test-proof-repo"
KE_CORE_REPOSITORY = "jweter/knowledge-engine-core"


@pytest.fixture()
def worker_module(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "controller_path_fix_proof_module", CONTROLLER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    base = tmp_path / "base"
    base.mkdir()
    (base / "handoffs").mkdir()
    monkeypatch.setattr(module, "BASE", base)
    monkeypatch.setattr(module, "HANDOFF_DIR", base / "handoffs")
    monkeypatch.setattr(
        module, "CONTROLLER_STATE", base / "controller-handoff-state.json"
    )
    monkeypatch.setattr(module, "SLICE_LEASE_DB", base / "leases.sqlite")
    return module


def _write_controller_state(worker_module, *, slices: list[dict]) -> None:
    ledger = {
        "controller": "iron-god-handoff-v1",
        "mode": "shadow_state_only",
        "slices": slices,
    }
    worker_module.CONTROLLER_STATE.write_text(json.dumps(ledger), encoding="utf-8")


def _write_handoff(
    worker_module, *, slice_id: str, allowed_paths: list[str], repository: str
) -> None:
    packet = {
        "schema_version": 1,
        "slice_id": slice_id,
        "state": "ELIGIBLE",
        "worktree_name": slice_id.lower() + "-wt",
        "branch": "iron-god/" + slice_id.lower(),
        "exact_base_sha": "a" * 40,
        "objective": "proof objective",
        "allowed_paths": allowed_paths,
        "forbidden": [],
        "acceptance": [],
        "repository": repository,
    }
    (worker_module.HANDOFF_DIR / (slice_id + ".json")).write_text(
        json.dumps(packet), encoding="utf-8"
    )


def test_runnable_handoffs_default_src_layout_backward_compatible(worker_module):
    _write_controller_state(
        worker_module, slices=[{"id": "IG-DEFAULT", "state": "ELIGIBLE"}]
    )
    _write_handoff(
        worker_module,
        slice_id="IG-DEFAULT",
        allowed_paths=["src/proof.py", "tests/test_proof.py"],
        repository=DEFAULT_REPOSITORY,
    )
    tasks = worker_module.runnable_handoffs()
    assert len(tasks) == 1
    assert tasks[0]["source"] == "src/proof.py"
    assert tasks[0]["tests"] == "tests/test_proof.py"


def test_runnable_handoffs_knowledge_engine_core_layout(worker_module):
    _write_controller_state(
        worker_module, slices=[{"id": "IG-KE-CORE", "state": "ELIGIBLE"}]
    )
    _write_handoff(
        worker_module,
        slice_id="IG-KE-CORE",
        allowed_paths=[
            "knowledge_engine/hybrid_search.py",
            "tests/test_hybrid_search.py",
        ],
        repository=KE_CORE_REPOSITORY,
    )
    tasks = worker_module.runnable_handoffs()
    assert len(tasks) == 1
    assert tasks[0]["source"] == "knowledge_engine/hybrid_search.py"
    assert tasks[0]["tests"] == "tests/test_hybrid_search.py"


def test_runnable_handoffs_ke_core_rejects_wrong_src_prefix(worker_module):
    _write_controller_state(
        worker_module, slices=[{"id": "IG-KE-WRONG", "state": "ELIGIBLE"}]
    )
    _write_handoff(
        worker_module,
        slice_id="IG-KE-WRONG",
        allowed_paths=["src/hybrid_search.py", "tests/test_hybrid_search.py"],
        repository=KE_CORE_REPOSITORY,
    )
    with pytest.raises(RuntimeError, match="HANDOFF_PATH_SHAPE_UNSUPPORTED"):
        worker_module.runnable_handoffs()


def test_runnable_handoffs_rejects_symlink_escape(worker_module):
    _write_controller_state(
        worker_module, slices=[{"id": "IG-ESCAPE", "state": "ELIGIBLE"}]
    )
    _write_handoff(
        worker_module,
        slice_id="IG-ESCAPE",
        allowed_paths=["src/escaped.py", "tests/test_proof.py"],
        repository=DEFAULT_REPOSITORY,
    )
    work = worker_module.BASE / "ig-escape-wt"
    work.mkdir()
    outside = worker_module.BASE.parent / "outside-root"
    outside.mkdir()
    try:
        (work / "src").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating a symlink is not permitted in this environment")
    with pytest.raises(RuntimeError, match="HANDOFF_PATH_ESCAPES_WORKTREE"):
        worker_module.runnable_handoffs()


def test_queued_repair_handoffs_default_src_layout_backward_compatible(worker_module):
    _write_controller_state(
        worker_module, slices=[{"id": "IG-REPAIR-DEFAULT", "state": "ELIGIBLE"}]
    )
    _write_handoff(
        worker_module,
        slice_id="IG-REPAIR-DEFAULT",
        allowed_paths=["src/proof.py", "tests/test_proof.py"],
        repository=DEFAULT_REPOSITORY,
    )
    lease = worker_module.claim_slice_lease(
        slice_id="IG-REPAIR-DEFAULT",
        base_sha="a" * 40,
        owner_pid=1,
        ttl_seconds=1800,
        path=worker_module.SLICE_LEASE_DB,
        now=1_700_000_000.0,
    )
    worker_module.record_failure_and_queue_repair(
        parent_slice_id="IG-REPAIR-DEFAULT",
        attempt_id=lease["attempt_id"],
        generation=lease["generation"],
        failure_signature="sig-1",
        reproduce="pytest -q",
        assertion_trace="trace",
        sanitized_log="log",
        environment_fingerprint="env",
        candidate_head="b" * 40,
        changed_paths=["src/proof.py"],
        suspected_root_cause="unknown",
        required_tests=["tests/test_proof.py"],
        path=worker_module.SLICE_LEASE_DB,
        now=1_700_000_000.0,
    )
    repairs = worker_module.queued_repair_handoffs(path=worker_module.SLICE_LEASE_DB)
    assert len(repairs) == 1
    assert repairs[0]["source"] == "src/proof.py"
    assert repairs[0]["tests"] == "tests/test_proof.py"
    assert repairs[0]["repair"] is True


def test_queued_repair_handoffs_knowledge_engine_core_layout(worker_module):
    _write_controller_state(
        worker_module, slices=[{"id": "IG-REPAIR-KE", "state": "ELIGIBLE"}]
    )
    _write_handoff(
        worker_module,
        slice_id="IG-REPAIR-KE",
        allowed_paths=[
            "knowledge_engine/hybrid_search.py",
            "tests/test_hybrid_search.py",
        ],
        repository=KE_CORE_REPOSITORY,
    )
    lease = worker_module.claim_slice_lease(
        slice_id="IG-REPAIR-KE",
        base_sha="a" * 40,
        owner_pid=1,
        ttl_seconds=1800,
        path=worker_module.SLICE_LEASE_DB,
        now=1_700_000_000.0,
    )
    worker_module.record_failure_and_queue_repair(
        parent_slice_id="IG-REPAIR-KE",
        attempt_id=lease["attempt_id"],
        generation=lease["generation"],
        failure_signature="sig-1",
        reproduce="pytest -q",
        assertion_trace="trace",
        sanitized_log="log",
        environment_fingerprint="env",
        candidate_head="b" * 40,
        changed_paths=["knowledge_engine/hybrid_search.py"],
        suspected_root_cause="unknown",
        required_tests=["tests/test_hybrid_search.py"],
        path=worker_module.SLICE_LEASE_DB,
        now=1_700_000_000.0,
    )
    repairs = worker_module.queued_repair_handoffs(path=worker_module.SLICE_LEASE_DB)
    assert len(repairs) == 1
    assert repairs[0]["source"] == "knowledge_engine/hybrid_search.py"
    assert repairs[0]["tests"] == "tests/test_hybrid_search.py"
    assert repairs[0]["repair"] is True
