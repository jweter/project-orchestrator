from project_orchestrator.failure_envelope import (
    build_failure_envelope,
    first_meaningful_diagnostic,
    retryability_for,
    sanitize_diagnostic,
)


def test_envelope_is_stable_across_line_number_drift():
    a = build_failure_envelope(
        repository="o/r",
        exact_head_sha="a" * 40,
        stage="ci:test",
        log_text="FAILED test_widget.py:13 AssertionError",
    )
    b = build_failure_envelope(
        repository="o/r",
        exact_head_sha="a" * 40,
        stage="ci:test",
        log_text="FAILED test_widget.py:99 AssertionError",
    )
    assert a["failure_fingerprint"] == b["failure_fingerprint"]
    assert a["failure_class"] == "TEST_FAILURE"


def test_diagnostic_redacts_user_paths_and_secrets():
    value = sanitize_diagnostic(
        r"Traceback C:\Users\jerem\secret\file.py token=supersecretvalue"
    )
    assert "jerem" not in value
    assert "supersecretvalue" not in value
    assert "<USER_HOME>" in value
    assert "<REDACTED>" in value


def test_first_meaningful_diagnostic_uses_existing_failure_classifier():
    diagnostic = first_meaningful_diagnostic(
        "noise\nINFO\nSyntaxError: unexpected character\nmore noise"
    )
    assert "SyntaxError: unexpected character" in diagnostic


def test_retryability_is_explicit():
    assert retryability_for("FORMAT") == "RETRYABLE"
    assert retryability_for("WORKER_TIMEOUT") == "RETRYABLE"
    assert retryability_for("SECURITY") == "BLOCKED_BY_AUTHORITY_OR_EXTERNAL_EVIDENCE"
    assert retryability_for("TEST_FAILURE") == "ENGINEERING_REPAIR_REQUIRED"
    assert retryability_for("UNKNOWN") == "UNKNOWN"


def test_missing_ci_log_produces_fail_closed_diagnostic_envelope():
    assert (
        first_meaningful_diagnostic(None) == "UNKNOWN: no meaningful failure line found"
    )
    envelope = build_failure_envelope(
        repository="jweter/knowledge-engine-ai",
        exact_head_sha="b" * 40,
        stage="ci:quality",
        log_text=None,
    )
    assert envelope["failure_class"] == "UNKNOWN"
    assert envelope["retryability"] == "UNKNOWN"
    assert envelope["first_meaningful_diagnostic"] == (
        "UNKNOWN: no meaningful failure line found"
    )
    assert len(envelope["failure_fingerprint"]) == 20
