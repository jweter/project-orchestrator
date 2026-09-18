import pytest

from project_orchestrator.product_reality_result import reconcile_product_reality_result


def debt(kind="packaged_app"):
    return {
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-core",
        "kind": kind,
        "state": "PENDING",
        "summary": "verify packaged application",
        "dependency_scope": "local-runtime",
        "source_ref": "issue:1",
    }


def result(status="PASS", **extra):
    return {
        "repository": "jweter/knowledge-engine-core",
        "status": status,
        "evidence_ref": "local://actor/job-1",
        "merge_authority": False,
        "product_reality_authority": False,
        **extra,
    }


def test_pass_verifies_objective_debt():
    reconciled = reconcile_product_reality_result(debt(), result())
    assert reconciled["debt"]["state"] == "VERIFIED"
    assert reconciled["debt"]["source_ref"] == "local://actor/job-1"


def test_failure_marks_failed_and_learning_candidate():
    reconciled = reconcile_product_reality_result(debt(), result("FAIL", failure_class="TEST_FAILURE"))
    assert reconciled["debt"]["state"] == "FAILED"
    assert reconciled["learning_candidate"] is True


def test_environment_failure_is_not_silently_verified():
    reconciled = reconcile_product_reality_result(debt(), result("ENVIRONMENT_FAILURE", failure_class="POETRY_UNAVAILABLE"))
    assert reconciled["debt"]["state"] == "FAILED"


def test_review_required_remains_pending():
    assert reconcile_product_reality_result(debt(), result("REVIEW_REQUIRED"))["debt"]["state"] == "PENDING"


def test_subjective_debt_cannot_be_machine_reconciled():
    with pytest.raises(ValueError, match="subjective"):
        reconcile_product_reality_result(debt("timing_acceptance"), result())


def test_repository_mismatch_fails_closed():
    with pytest.raises(ValueError, match="repository"):
        reconcile_product_reality_result(debt(), result(repository="jweter/Project-Everward"))
