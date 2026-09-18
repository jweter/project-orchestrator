import json

import pytest

from project_orchestrator.product_reality_queue import queue_product_reality_job


def debt(kind="packaged_app", state="PENDING"):
    return {
        "debt_id": "debt-1",
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-core",
        "kind": kind,
        "state": state,
        "summary": "bounded local product evidence",
        "dependency_scope": "local-runtime",
        "source_ref": "issue:1",
    }


SHA = "a" * 40


def test_queues_dispatchable_job_atomically(tmp_path):
    result = queue_product_reality_job(debt(), branch="feature/x", base_sha=SHA, queue_dir=tmp_path, verification=["python -m pytest -q"])
    assert result["status"] == "QUEUED"
    payload = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert payload["base_sha"] == SHA
    assert payload["merge_authority"] is False
    assert payload["product_reality_authority"] is False
    assert payload["verification"] == ["python -m pytest -q"]


def test_queue_is_idempotent(tmp_path):
    first = queue_product_reality_job(debt(), branch="feature/x", base_sha=SHA, queue_dir=tmp_path, verification=["verify"])
    second = queue_product_reality_job(debt(), branch="feature/x", base_sha=SHA, queue_dir=tmp_path, verification=["verify"])
    assert first["job"]["job_id"] == second["job"]["job_id"]
    assert second["status"] == "ALREADY_QUEUED"


def test_human_evidence_never_queued(tmp_path):
    result = queue_product_reality_job(debt("visual_acceptance"), branch="feature/x", base_sha=SHA, queue_dir=tmp_path, verification=["verify"])
    assert result["status"] == "HUMAN_EVIDENCE_REQUIRED"
    assert list(tmp_path.glob("*")) == []


def test_missing_verification_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="bounded verification"):
        queue_product_reality_job(debt(), branch="feature/x", base_sha=SHA, queue_dir=tmp_path)
