from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from project_orchestrator.evolution_bridge import (
    EvolutionBridgeError,
    build_worker_handoff,
    execute_worker_safe_repair,
    normalize_worker_result,
    record_verified_repair_lesson,
)
from project_orchestrator.evolution_cli import main


SHA = "a" * 40


def _memory(entries=None):
    return {"schema_version": 1, "entries": entries or []}


def _worker(**overrides):
    base = {
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-core",
        "branch": "main",
        "head_sha": SHA,
        "probe_id": "canonical-preflight",
        "pattern_id": "preflight-format-regression",
        "evidence_ref": "worker:core:1",
        "observed_at": "2026-09-13T10:00:00-05:00",
        "status": "PASS",
        "summary": "Canonical exact-head verification completed.",
        "sanitized_log_text": "",
    }
    base.update(overrides)
    return base


def _format_control_plane(command=None):
    return {
        "deterministic_repair": {
            "approved_classes": ["FORMAT"],
            "commands": {
                "FORMAT": [
                    command
                    or [sys.executable, "-c", "print('format repair applied')"]
                ]
            },
        }
    }


def test_worker_pass_routes_to_verified_pass() -> None:
    handoff = build_worker_handoff(
        worker_result=_worker(),
        control_plane={},
        memory=_memory(),
    )
    assert handoff["next_action"] == "VERIFIED_PASS"
    assert handoff["repair_plan"] is None
    assert handoff["evolution_plan"] is not None


def test_format_failure_reuses_repository_authorized_safe_repair() -> None:
    handoff = build_worker_handoff(
        worker_result=_worker(
            status="FAIL",
            sanitized_log_text="ruff format: file would be reformatted",
        ),
        control_plane=_format_control_plane(),
        memory=_memory(),
    )
    assert handoff["next_action"] == "SAFE_AUTO_REPAIR_AVAILABLE"
    assert handoff["repair_plan"]["failure_class"] == "FORMAT"
    assert handoff["repair_plan"]["eligible_for_auto_repair"] is True
    assert handoff["independent_verification_required"] is True


def test_behavioral_test_failure_requires_engineering_repair() -> None:
    handoff = build_worker_handoff(
        worker_result=_worker(
            status="FAIL",
            sanitized_log_text="FAILED tests/test_contract.py::test_boundary AssertionError",
        ),
        control_plane={},
        memory=_memory(),
    )
    assert handoff["next_action"] == "ENGINEERING_REPAIR_REQUIRED"
    assert handoff["repair_plan"]["failure_class"] == "TEST_FAILURE"
    assert handoff["repair_plan"]["eligible_for_auto_repair"] is False


def test_environment_worker_status_never_becomes_product_repair() -> None:
    handoff = build_worker_handoff(
        worker_result=_worker(status="ENVIRONMENT_FAILURE"),
        control_plane=_format_control_plane(),
        memory=_memory(),
    )
    assert handoff["next_action"] == "ENVIRONMENT_RETRY"
    assert handoff["repair_plan"] is None
    assert handoff["evolution_plan"] is None


def test_environment_signature_inside_fail_still_routes_to_environment_retry() -> None:
    handoff = build_worker_handoff(
        worker_result=_worker(
            status="FAIL",
            sanitized_log_text="runner environment limitation: command not found",
        ),
        control_plane=_format_control_plane(),
        memory=_memory(),
    )
    assert handoff["repair_plan"]["failure_class"] == "ENVIRONMENT"
    assert handoff["next_action"] == "ENVIRONMENT_RETRY"


def test_product_reality_remains_lane_scoped_human_gate() -> None:
    handoff = build_worker_handoff(
        worker_result=_worker(status="PRODUCT_REALITY_REQUIRED"),
        control_plane={},
        memory=_memory(),
    )
    assert handoff["next_action"] == "PRODUCT_REALITY_REQUIRED"
    assert handoff["repair_plan"] is None


def test_review_required_does_not_invent_root_cause() -> None:
    handoff = build_worker_handoff(
        worker_result=_worker(status="REVIEW_REQUIRED"),
        control_plane={},
        memory=_memory(),
    )
    assert handoff["next_action"] == "GATHER_EVIDENCE"
    assert handoff["evolution_plan"] is None


def test_reserved_human_decision_overrides_automatic_action() -> None:
    handoff = build_worker_handoff(
        worker_result=_worker(
            status="FAIL",
            reserved_human_decision=True,
            sanitized_log_text="ruff format: file would be reformatted",
        ),
        control_plane=_format_control_plane(),
        memory=_memory(),
    )
    assert handoff["next_action"] == "HUMAN_DECISION_REQUIRED"
    assert handoff["repair_plan"] is None


def test_worker_identity_is_exact_and_project_isolated() -> None:
    with pytest.raises(EvolutionBridgeError, match="does not belong"):
        normalize_worker_result(
            _worker(
                project="everward",
                repository="jweter/knowledge-engine-core",
            )
        )
    with pytest.raises(EvolutionBridgeError, match="40-character"):
        normalize_worker_result(_worker(head_sha="abc"))


def test_worker_evidence_fails_closed_on_secret_material() -> None:
    with pytest.raises(EvolutionBridgeError, match="secret material"):
        normalize_worker_result(
            _worker(sanitized_log_text="unexpected token=ghp_abcdefghijklmnopqrstuvwxyz")
        )


def test_execute_safe_repair_requires_independent_verification(tmp_path: Path) -> None:
    handoff = build_worker_handoff(
        worker_result=_worker(
            status="FAIL",
            sanitized_log_text="ruff format: file would be reformatted",
        ),
        control_plane=_format_control_plane(),
        memory=_memory(),
    )
    result = execute_worker_safe_repair(handoff, repo_root=tmp_path)
    assert result["repair_execution"]["execution_status"] == "REPAIR_COMMANDS_APPLIED"
    assert result["next_action"] == "VERIFY_REPAIR"
    assert result["independent_verification_required"] is True


def test_non_safe_handoff_cannot_execute_mutating_repair(tmp_path: Path) -> None:
    handoff = build_worker_handoff(
        worker_result=_worker(
            status="FAIL",
            sanitized_log_text="FAILED tests/test_x.py::test_y AssertionError",
        ),
        control_plane={},
        memory=_memory(),
    )
    result = execute_worker_safe_repair(handoff, repo_root=tmp_path)
    assert result["repair_execution"] is None
    assert result["next_action"] == "ENGINEERING_REPAIR_REQUIRED"


def test_verified_repair_can_be_persisted_as_sanitized_learning_memory() -> None:
    updated = record_verified_repair_lesson(
        memory=_memory(),
        project="everward",
        repository="jweter/Project-Everward",
        source_ref="everward#251@f19ac81",
        symptom="Adapter accepted authoritative status that renderer rejected.",
        root_cause="Shared status contract changed without validating the renderer consumer.",
        fix="Extended renderer status contract and debt semantics.",
        verification="Full adapter-to-renderer regression plus exact-head CI passed.",
        regression_protection="Exercise every authoritative status through the full adapter-to-renderer path.",
        residual_risk="Future consumers must remain contract-tested.",
    )
    assert len(updated["entries"]) == 1
    assert updated["entries"][0]["evidence_state"] == "VERIFIED"
    assert updated["entries"][0]["project"] == "everward"


def test_recurrence_can_escalate_bridge_evolution_plan() -> None:
    prior = {
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-core",
        "pattern_id": "preflight-format-regression",
        "evidence_ref": "worker:core:old",
        "evidence_state": "VERIFIED",
        "event_type": "FAILURE",
        "root_cause_verified": True,
        "repair_verified": True,
        "mechanism_brittle": True,
        "prevention_verified": False,
        "redesign_verified": False,
        "reserved_human_decision": False,
        "safety_regression": False,
        "learning_effect": "NO_CHANGE",
        "occurred_after_prevention": False,
    }
    handoff = build_worker_handoff(
        worker_result=_worker(
            status="FAIL",
            root_cause_verified=True,
            repair_verified=True,
            mechanism_brittle=True,
            sanitized_log_text="ruff format: file would be reformatted",
        ),
        control_plane=_format_control_plane(),
        memory=_memory(),
        prior_events=[prior],
    )
    assert handoff["evolution_plan"]["current_stage"] == "REPLACEMENT"
    assert handoff["evolution_plan"]["failure_recurrence_count"] == 2


def test_prevention_plus_measured_improvement_can_reach_evolution_verified() -> None:
    prior_failure = {
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-core",
        "pattern_id": "preflight-format-regression",
        "evidence_ref": "worker:core:old",
        "evidence_state": "VERIFIED",
        "event_type": "FAILURE",
        "root_cause_verified": True,
        "repair_verified": True,
        "mechanism_brittle": False,
        "prevention_verified": False,
        "redesign_verified": False,
        "reserved_human_decision": False,
        "safety_regression": False,
        "learning_effect": "UNKNOWN",
        "occurred_after_prevention": False,
    }
    handoff = build_worker_handoff(
        worker_result=_worker(
            status="PASS",
            prevention_verified=True,
            learning_effect="PREVENTED",
        ),
        control_plane={},
        memory=_memory(),
        prior_events=[prior_failure],
        measurements=[
            {
                "repository": "jweter/knowledge-engine-core",
                "pattern_id": "preflight-format-regression",
                "evidence_ref": "metric:repeat-failure-rate",
                "evidence_state": "VERIFIED",
                "metric": "repeat_failure_rate",
                "direction": "lower_is_better",
                "before": 0.5,
                "after": 0.0,
                "sample_count_before": 4,
                "sample_count_after": 4,
            }
        ],
    )
    assert handoff["next_action"] == "EVOLUTION_VERIFIED"
    assert handoff["evolution_plan"]["evolution_eligible"] is True


def test_worker_handoff_cli_emits_machine_readable_action(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "handoff.json"
    input_path.write_text(
        json.dumps(
            {
                "worker_result": _worker(),
                "control_plane": {},
                "memory": _memory(),
            }
        ),
        encoding="utf-8",
    )
    assert main(["worker-handoff", "--input", str(input_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["next_action"] == "VERIFIED_PASS"


def test_record_verified_lesson_cli_returns_updated_memory(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "lesson.json"
    input_path.write_text(
        json.dumps(
            {
                "memory": _memory(),
                "project": "rocksmith",
                "repository": "jweter/rocksmith-cdlc-generator",
                "source_ref": "rocksmith#455",
                "symptom": "Repeated score/audio phase error.",
                "root_cause": "Custom timing path diverged from the mature reference semantics.",
                "fix": "Use the verified EOF-derived structural path.",
                "verification": "Differential regression passed.",
                "regression_protection": "Keep shared timing parity fixtures.",
                "residual_risk": "Private Product Reality remains separate.",
            }
        ),
        encoding="utf-8",
    )
    assert main(["record-verified-lesson", "--input", str(input_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert len(output["entries"]) == 1
    assert output["entries"][0]["project"] == "rocksmith"


def test_evolution_verified_compiles_scheduler_capability() -> None:
    from project_orchestrator.evolution_bridge import compile_verified_capability

    prior_failure = {
        "project": "knowledge-engine", "repository": "jweter/knowledge-engine-core",
        "pattern_id": "preflight-format-regression", "evidence_ref": "worker:old",
        "evidence_state": "VERIFIED", "event_type": "FAILURE",
        "root_cause_verified": True, "repair_verified": True, "mechanism_brittle": False,
        "prevention_verified": False, "redesign_verified": False,
        "reserved_human_decision": False, "safety_regression": False,
    }
    worker = _worker(status="PASS", prevention_verified=True, learning_effect="PREVENTED")
    handoff = build_worker_handoff(
        worker_result=worker, control_plane={}, memory=_memory(), prior_events=[prior_failure],
        measurements=[{"repository":"jweter/knowledge-engine-core","pattern_id":"preflight-format-regression",
        "evidence_ref":"metric:rate","evidence_state":"VERIFIED","metric":"repeat_failure_rate",
        "direction":"lower_is_better","before":0.5,"after":0.0,"sample_count_before":4,"sample_count_after":4}],
    )
    capability = compile_verified_capability(
        worker_result=worker, evolution_plan=handoff["evolution_plan"],
        mechanism="run formatter before exact-head preflight",
        applicability={"failure_classes":["FORMAT"],"path_prefixes":["src/"]},
    )
    assert capability["status"] == "VERIFIED_RETAINED"
    assert capability["authority"] == "ADVISORY_TO_SCHEDULER_NORMAL_GATES_REQUIRED"
    assert capability["measurement_summary"][0]["outcome"] == "IMPROVED"


def test_capability_compiler_fails_closed_before_evolution_verified() -> None:
    from project_orchestrator.evolution_bridge import compile_verified_capability

    handoff = build_worker_handoff(worker_result=_worker(), control_plane={}, memory=_memory())
    with pytest.raises(EvolutionBridgeError, match="evolution_eligible"):
        compile_verified_capability(
            worker_result=_worker(), evolution_plan=handoff["evolution_plan"],
            mechanism="do something", applicability={"failure_classes":["FORMAT"]},
        )
