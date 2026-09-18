import pytest

from project_orchestrator.product_reality_learning import reconcile_and_learn


def debt():
    return {
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-core",
        "kind": "packaged_app",
        "state": "PENDING",
        "summary": "verify packaged app",
        "dependency_scope": "local-runtime",
        "source_ref": "issue:1",
    }


def failure():
    return {
        "repository": "jweter/knowledge-engine-core",
        "status": "ENVIRONMENT_FAILURE",
        "failure_class": "ENVIRONMENT_FAILURE",
        "evidence_ref": "local://actor/job-1",
        "merge_authority": False,
        "product_reality_authority": False,
    }


def diagnosis():
    return {
        "log_text": "poetry: command not found",
        "root_cause": "Poetry was unavailable in the clean verification environment.",
        "fix": "Provision the locked Poetry environment before preflight.",
        "verification": "Deterministic environment provisioning and exact-head preflight passed.",
        "regression_protection": "Environment provisioning runs before expensive verification.",
        "residual_risk": "External package registry availability remains outside repository control.",
    }


def test_failure_requires_verified_diagnosis_before_learning():
    result = reconcile_and_learn({"schema_version": 1, "entries": []}, debt(), failure())
    assert result["learning_status"] == "DIAGNOSIS_REQUIRED"
    assert result["memory"]["entries"] == []


def test_verified_failure_persists_lesson():
    result = reconcile_and_learn({"schema_version": 1, "entries": []}, debt(), failure(), diagnosis=diagnosis())
    assert result["learning_status"] == "VERIFIED_LESSON_PERSISTED"
    assert len(result["memory"]["entries"]) == 1
    assert result["memory"]["entries"][0]["evidence_state"] == "VERIFIED"


def test_success_does_not_create_failure_lesson():
    ok = failure() | {"status": "PASS", "failure_class": None}
    result = reconcile_and_learn({"schema_version": 1, "entries": []}, debt(), ok)
    assert result["learning_status"] == "NOT_APPLICABLE"
    assert result["memory"]["entries"] == []


def test_incomplete_diagnosis_fails_closed():
    bad = diagnosis()
    bad["fix"] = ""
    with pytest.raises(ValueError, match="missing fields"):
        reconcile_and_learn({"schema_version": 1, "entries": []}, debt(), failure(), diagnosis=bad)
