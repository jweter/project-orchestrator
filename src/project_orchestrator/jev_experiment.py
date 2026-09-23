from __future__ import annotations

from typing import Any

from .jev_evidence import evaluate_records, load_jsonl


def build_experiment_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    base = evaluate_records(records)
    neural = [r for r in records if r.get("neural_call_made") is True]
    completed_neural = [r for r in neural if isinstance(r.get("outcome"), dict)]
    pending = [r for r in records if r.get("outcome") is None]
    states: dict[str, int] = {}
    for record in records:
        state = str(record.get("shadow_state", "UNKNOWN"))
        states[state] = states.get(state, 0) + 1
    live_ready = bool(neural)
    return {
        "schema_version": 1,
        "experiment": "JEV_SHADOW_BASELINE",
        "records_total": len(records),
        "open_outcomes": len(pending),
        "neural_observations": len(neural),
        "completed_neural_observations": len(completed_neural),
        "shadow_states": dict(sorted(states.items())),
        "comparison": base,
        "live_inference_observed": live_ready,
        "baseline_state": (
            "MEASURED_LIVE_BASELINE" if completed_neural
            else "LIVE_OBSERVATION_PENDING" if neural
            else "NO_LIVE_JEV_OBSERVATIONS"
        ),
        "next_gate": (
            "EVALUATE_CALIBRATION_AND_ROUTING_QUALITY" if completed_neural
            else "WAIT_FOR_TERMINAL_OUTCOMES" if neural
            else "ENABLE_EXPLICIT_LIVE_SHADOW_PROVIDER"
        ),
        "promotion_authority": False,
        "warning": (
            None if neural
            else "Current records do not contain a neural Jev call; deterministic SHADOW_PENDING requests are infrastructure evidence, not Jev performance evidence."
        ),
    }


def report_from_ledger(path) -> dict[str, Any]:
    return build_experiment_report(load_jsonl(path))
