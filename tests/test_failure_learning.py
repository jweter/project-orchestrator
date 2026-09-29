import pytest

from project_orchestrator.failure_learning import persist_verified_failure, verified_failure_lesson


def empty():
    return {"schema_version": 1, "entries": []}


def args():
    return {
        "repository": "jweter/knowledge-engine-core",
        "source_ref": "pr:505",
        "log_text": "pytest FAILED tests/test_worker.py::test_exact_sha",
        "root_cause": "untracked files were omitted from checkout cleanliness validation",
        "fix": "reject non-ignored untracked files before exact-SHA verification",
        "verification": "canonical preflight and fresh CI passed",
        "regression_protection": "worker regression test covers untracked files",
        "residual_risk": "ignored files remain governed by repository ignore policy",
    }


def test_verified_failure_becomes_sanitized_learning():
    lesson = verified_failure_lesson(**args())
    assert lesson["project"] == "knowledge-engine"
    assert lesson["evidence_state"] == "VERIFIED"
    assert lesson["symptom"].startswith("TEST_FAILURE:")


def test_unknown_failure_fails_closed():
    values = args()
    values["log_text"] = "something odd happened"
    with pytest.raises(ValueError, match="not deterministic enough"):
        verified_failure_lesson(**values)


def test_requires_verified_diagnosis_fix_and_verification():
    values = args()
    values["verification"] = ""
    with pytest.raises(ValueError, match="required"):
        verified_failure_lesson(**values)


def test_persist_is_idempotent():
    first = persist_verified_failure(empty(), **args())
    second = persist_verified_failure(first, **args())
    assert len(second["entries"]) == 1


def test_control_plane_failure_can_become_verified_portfolio_learning():
    values = args()
    values["repository"] = "jweter/project-orchestrator"
    values["source_ref"] = "pr:453"
    lesson = verified_failure_lesson(**values)
    assert lesson["project"] == "portfolio"
    assert lesson["repository"] == "jweter/project-orchestrator"


def test_unknown_repository_rejected():
    values = args()
    values["repository"] = "jweter/not-authorized"
    with pytest.raises(ValueError, match="not an authorized learning repository"):
        verified_failure_lesson(**values)
