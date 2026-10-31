from pathlib import Path

from engineering.validate_supervisor_architecture import (
    MAX_HELPER_LINES,
    MAX_SOURCE_LINES,
    validate,
)


def test_current_supervisor_stays_within_architecture_budget():
    assert validate() == []


def test_validator_rejects_source_line_regrowth(tmp_path):
    source = "def main():\n    return 0\n" + ("# filler\n" * (MAX_SOURCE_LINES + 1))
    path = tmp_path / "supervisor.py"
    path.write_text(source, encoding="utf-8")
    errors = validate(path)
    assert any("source budget exceeded" in error for error in errors)


def test_validator_rejects_large_non_orchestration_helper(tmp_path):
    helper_body = "\n".join(f"    x{i} = {i}" for i in range(MAX_HELPER_LINES + 1))
    source = (
        "def oversized():\n"
        + helper_body
        + "\n\ndef main():\n    return 0\n"
    )
    path = tmp_path / "supervisor.py"
    path.write_text(source, encoding="utf-8")
    errors = validate(path)
    assert any("non-orchestration helper too large" in error for error in errors)


def test_productive_liveness_sidecar_contention_is_nonfatal_and_visible():
    source = (Path(__file__).parents[1] / "scripts" / "continuous_flow_supervisor.py").read_text(encoding="utf-8")
    # Supervisor owns nonfatal publication; extracted helper owns degradation.
    assert 'liveness_published=optional_atomic(state/"productive-liveness.json"' in source
    assert "_throughput.degraded_reasons(" in source
    assert "liveness_published=liveness_published" in source


def test_productive_liveness_publication_failure_degrades_health_verdict():
    from project_orchestrator.supervisor_throughput_runtime import degraded_reasons

    source = (Path(__file__).parents[1] / "scripts" / "continuous_flow_supervisor.py").read_text(encoding="utf-8")
    reasons = degraded_reasons(
        lifecycle_recovery={"publication_blocked": False},
        liveness_published=False,
        workspace_provisioning={"status": "OK"},
        convergence={"status": "CONVERGED"},
        idle_streaks={},
        liveness={"status": "HEALTHY"},
        delivery={"status": "HEALTHY"},
        builder_ok=True,
        builder_status="PR_OPEN",
        rc=0,
        linear_rc=0,
        quarantines={},
    )
    assert {"reason": "PRODUCTIVE_LIVENESS_PUBLICATION_BLOCKED"} in reasons
    assert reasons == [{"reason": "PRODUCTIVE_LIVENESS_PUBLICATION_BLOCKED"}]
    assert "healthy=not degraded_reasons" in source
