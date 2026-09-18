import pytest

from project_orchestrator.product_reality_dispatch import dispatch_plan


def debt(kind="unreal_playtest", state="PENDING"):
    return {
        "project": "everward",
        "repository": "jweter/Project-Everward",
        "kind": kind,
        "state": state,
        "summary": "headless packaged smoke needs real environment evidence",
        "dependency_scope": "packaged-smoke",
        "source_ref": "issue:240",
    }


def test_automatable_debt_becomes_bounded_job_without_authority():
    result = dispatch_plan(debt(), branch="main", base_sha="a" * 40)
    assert result["status"] == "DISPATCHABLE"
    assert result["job"]["product_reality_authority"] is False
    assert result["job"]["merge_authority"] is False
    assert result["job"]["purpose"] == "product_reality_evidence_collection"


def test_subjective_acceptance_stays_human():
    result = dispatch_plan(debt(kind="timing_acceptance"), branch="main", base_sha="a" * 40)
    assert result["status"] == "HUMAN_EVIDENCE_REQUIRED"
    assert "job" not in result


def test_verified_debt_is_not_redispatched():
    result = dispatch_plan(debt(state="VERIFIED"), branch="main", base_sha="a" * 40)
    assert result["status"] == "NO_ACTIVE_DEBT"


def test_exact_sha_required_for_dispatch():
    with pytest.raises(ValueError, match="40-character"):
        dispatch_plan(debt(), branch="main", base_sha="short")


def test_job_identity_is_deterministic():
    first = dispatch_plan(debt(), branch="main", base_sha="a" * 40)
    second = dispatch_plan(debt(), branch="main", base_sha="a" * 40)
    assert first["job"]["job_id"] == second["job"]["job_id"]
