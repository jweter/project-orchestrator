from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .kpi_telemetry import ALLOWED_REPOSITORIES, append_event, load_events, validate_event

SCHEMA_VERSION = 1
DEFAULT_SUITE = Path("engineering/agent-eval-suite.json")
DEFAULT_LEDGER = Path("engineering/kpi-events.jsonl")
CHECK_STAGES = frozenset({"PREFLIGHT", "PR_CI"})
CHECK_OUTCOMES = frozenset({"PASS", "FAIL"})
ACCEPTANCE_OUTCOMES = frozenset({"PASS", "FAIL"})
ACTORS = frozenset({"ORCHESTRATOR", "EXTERNAL_AUTOMATION", "HUMAN"})


class AgentEvalError(ValueError):
    """Raised when agent-evaluation evidence is malformed or incomparable."""


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AgentEvalError(f"{field} must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgentEvalError(f"{field} is not valid ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise AgentEvalError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_nonempty(item) for item in value)


def load_suite(path: str | Path = DEFAULT_SUITE) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_suite(value)


def validate_suite(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgentEvalError("agent-eval suite must be an object")
    required = {
        "schema_version",
        "suite_id",
        "purpose",
        "advisory_only",
        "product_reality_may_be_inferred",
        "canonical_kpi_ledger",
        "tasks",
    }
    if set(raw) != required:
        raise AgentEvalError(f"agent-eval suite fields must be exactly {sorted(required)}")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise AgentEvalError("agent-eval suite schema_version must be 1")
    if not _nonempty(raw["suite_id"]) or not _nonempty(raw["purpose"]):
        raise AgentEvalError("suite_id and purpose must be non-empty strings")
    if raw["advisory_only"] is not True:
        raise AgentEvalError("agent-eval results must remain advisory_only=true")
    if raw["product_reality_may_be_inferred"] is not False:
        raise AgentEvalError("agent-eval must never infer Product Reality")
    if raw["canonical_kpi_ledger"] != "engineering/kpi-events.jsonl":
        raise AgentEvalError("agent-eval must use the canonical KPI event ledger")
    tasks = raw["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise AgentEvalError("tasks must be a non-empty list")

    normalized_tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise AgentEvalError(f"task {index} must be an object")
        fields = {
            "task_id",
            "title",
            "repository",
            "acceptance_kind",
            "prompt",
            "baseline_ref",
            "fault_fixture_ref",
            "measurement_contract_ref",
            "verification_commands",
            "required_stages",
        }
        if set(task) != fields:
            raise AgentEvalError(f"task {index} fields must be exactly {sorted(fields)}")
        task_id = task["task_id"]
        if not _nonempty(task_id) or task_id in seen:
            raise AgentEvalError("task_id values must be unique non-empty strings")
        seen.add(task_id)
        if not _nonempty(task["title"]) or not _nonempty(task["prompt"]):
            raise AgentEvalError(f"{task_id}: title and prompt must be non-empty")
        if task["repository"] not in ALLOWED_REPOSITORIES:
            raise AgentEvalError(f"{task_id}: unsupported repository {task['repository']!r}")
        if task["acceptance_kind"] != "DETERMINISTIC":
            raise AgentEvalError(f"{task_id}: v1 supports deterministic acceptance only")
        if (
            not _nonempty(task["baseline_ref"])
            or len(task["baseline_ref"]) != 40
            or any(char not in "0123456789abcdefABCDEF" for char in task["baseline_ref"])
        ):
            raise AgentEvalError(f"{task_id}: baseline_ref must be an exact 40-character commit SHA")
        if not _nonempty(task["fault_fixture_ref"]):
            raise AgentEvalError(f"{task_id}: fault_fixture_ref is required")
        if not _nonempty(task["measurement_contract_ref"]):
            raise AgentEvalError(f"{task_id}: measurement_contract_ref is required")
        if not _nonempty_list(task["verification_commands"]):
            raise AgentEvalError(f"{task_id}: verification_commands must be non-empty")
        required_stages = task["required_stages"]
        if (
            not isinstance(required_stages, list)
            or not required_stages
            or any(stage not in CHECK_STAGES for stage in required_stages)
            or len(set(required_stages)) != len(required_stages)
        ):
            raise AgentEvalError(f"{task_id}: required_stages must be unique PREFLIGHT/PR_CI values")
        normalized_tasks.append(dict(task))

    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": raw["suite_id"],
        "purpose": raw["purpose"],
        "advisory_only": True,
        "product_reality_may_be_inferred": False,
        "canonical_kpi_ledger": raw["canonical_kpi_ledger"],
        "tasks": normalized_tasks,
    }


def validate_suite_files(suite: dict[str, Any], root: str | Path = ".") -> dict[str, Any]:
    """Verify that pinned deterministic task fixtures are present locally."""
    suite = validate_suite(suite)
    root_path = Path(root)
    missing = [
        task["fault_fixture_ref"]
        for task in suite["tasks"]
        if not (root_path / task["fault_fixture_ref"]).is_file()
    ]
    if missing:
        raise AgentEvalError(f"missing agent-eval fault fixtures: {sorted(missing)}")
    return suite


def _suite_task(suite: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in suite["tasks"]:
        if task["task_id"] == task_id:
            return task
    raise AgentEvalError(f"unknown task_id for suite {suite['suite_id']}: {task_id}")


def validate_attempt(raw: Any, suite: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgentEvalError("agent-eval attempt must be an object")
    required = {
        "schema_version",
        "suite_id",
        "task_id",
        "evaluation_run_id",
        "agent_id",
        "agent_config_id",
        "actor",
        "repository",
        "baseline_ref",
        "measurement_contract_ref",
        "started_at",
        "checks",
        "repairs",
        "human_interventions",
        "acceptance",
        "measured_cost",
        "evidence_refs",
    }
    if set(raw) != required:
        raise AgentEvalError(f"agent-eval attempt fields must be exactly {sorted(required)}")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise AgentEvalError("attempt schema_version must be 1")
    if raw["suite_id"] != suite["suite_id"]:
        raise AgentEvalError("attempt suite_id does not match suite")
    task = _suite_task(suite, raw["task_id"])
    for field in ("evaluation_run_id", "agent_id", "agent_config_id", "baseline_ref"):
        if not _nonempty(raw[field]):
            raise AgentEvalError(f"{field} must be a non-empty string")
    if raw["actor"] not in ACTORS:
        raise AgentEvalError(f"unsupported actor: {raw['actor']!r}")
    if raw["repository"] != task["repository"]:
        raise AgentEvalError("attempt repository does not match task repository")
    if raw["baseline_ref"] != task["baseline_ref"]:
        raise AgentEvalError("attempt baseline_ref does not match the pinned task baseline")
    if raw["measurement_contract_ref"] != task["measurement_contract_ref"]:
        raise AgentEvalError("attempt measurement_contract_ref does not match task")
    _parse_time(raw["started_at"], "started_at")
    if not _nonempty_list(raw["evidence_refs"]):
        raise AgentEvalError("attempt evidence_refs must be a non-empty list")

    checks = raw["checks"]
    if not isinstance(checks, list):
        raise AgentEvalError("checks must be a list")
    normalized_checks: list[dict[str, Any]] = []
    seen_check_keys: set[tuple[str, int]] = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) != {
            "stage",
            "attempt",
            "observed_at",
            "outcome",
            "evidence_refs",
        }:
            raise AgentEvalError("each check must contain stage, attempt, observed_at, outcome, evidence_refs")
        if check["stage"] not in CHECK_STAGES:
            raise AgentEvalError(f"unsupported check stage: {check['stage']!r}")
        attempt_number = check["attempt"]
        if isinstance(attempt_number, bool) or not isinstance(attempt_number, int) or attempt_number < 1:
            raise AgentEvalError("check attempt must be a positive integer")
        key = (check["stage"], attempt_number)
        if key in seen_check_keys:
            raise AgentEvalError(f"duplicate check stage/attempt: {key}")
        seen_check_keys.add(key)
        _parse_time(check["observed_at"], "check.observed_at")
        if check["outcome"] not in CHECK_OUTCOMES:
            raise AgentEvalError(f"unsupported check outcome: {check['outcome']!r}")
        if not _nonempty_list(check["evidence_refs"]):
            raise AgentEvalError("check evidence_refs must be non-empty")
        normalized_checks.append(dict(check))

    repairs = raw["repairs"]
    if not isinstance(repairs, list):
        raise AgentEvalError("repairs must be a list")
    normalized_repairs: list[dict[str, Any]] = []
    for repair in repairs:
        if not isinstance(repair, dict) or set(repair) != {
            "attempt",
            "observed_at",
            "failure_class",
            "evidence_refs",
        }:
            raise AgentEvalError("each repair must contain attempt, observed_at, failure_class, evidence_refs")
        attempt_number = repair["attempt"]
        if isinstance(attempt_number, bool) or not isinstance(attempt_number, int) or attempt_number < 1:
            raise AgentEvalError("repair attempt must be a positive integer")
        if not _nonempty(repair["failure_class"]):
            raise AgentEvalError("repair failure_class must be a non-empty string")
        _parse_time(repair["observed_at"], "repair.observed_at")
        if not _nonempty_list(repair["evidence_refs"]):
            raise AgentEvalError("repair evidence_refs must be non-empty")
        normalized_repairs.append(dict(repair))

    interventions = raw["human_interventions"]
    if not isinstance(interventions, list):
        raise AgentEvalError("human_interventions must be a list")
    normalized_interventions: list[dict[str, Any]] = []
    for intervention in interventions:
        if not isinstance(intervention, dict) or set(intervention) != {
            "observed_at",
            "reason",
            "evidence_refs",
        }:
            raise AgentEvalError("each human intervention must contain observed_at, reason, evidence_refs")
        _parse_time(intervention["observed_at"], "human_intervention.observed_at")
        if not _nonempty(intervention["reason"]) or not _nonempty_list(intervention["evidence_refs"]):
            raise AgentEvalError("human intervention reason/evidence_refs must be non-empty")
        normalized_interventions.append(dict(intervention))

    acceptance = raw["acceptance"]
    normalized_acceptance = None
    if acceptance is not None:
        if not isinstance(acceptance, dict) or set(acceptance) != {"observed_at", "outcome", "evidence_refs"}:
            raise AgentEvalError("acceptance must be null or contain observed_at, outcome, evidence_refs")
        _parse_time(acceptance["observed_at"], "acceptance.observed_at")
        if acceptance["outcome"] not in ACCEPTANCE_OUTCOMES:
            raise AgentEvalError(f"unsupported acceptance outcome: {acceptance['outcome']!r}")
        if not _nonempty_list(acceptance["evidence_refs"]):
            raise AgentEvalError("acceptance evidence_refs must be non-empty")
        normalized_acceptance = dict(acceptance)

    measured_cost = raw["measured_cost"]
    normalized_cost = None
    if measured_cost is not None:
        if not isinstance(measured_cost, dict) or set(measured_cost) != {"value", "currency", "evidence_refs"}:
            raise AgentEvalError("measured_cost must be null or contain value, currency, evidence_refs")
        value = measured_cost["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise AgentEvalError("measured_cost.value must be a non-negative number")
        if not _nonempty(measured_cost["currency"]) or not _nonempty_list(measured_cost["evidence_refs"]):
            raise AgentEvalError("measured_cost currency/evidence_refs must be non-empty")
        normalized_cost = dict(measured_cost)

    started = _parse_time(raw["started_at"], "started_at")
    all_times = [
        _parse_time(item["observed_at"], "observed_at")
        for item in normalized_checks + normalized_repairs + normalized_interventions
    ]
    if normalized_acceptance is not None:
        all_times.append(_parse_time(normalized_acceptance["observed_at"], "acceptance.observed_at"))
    if any(item < started for item in all_times):
        raise AgentEvalError("attempt evidence cannot predate started_at")

    required_stages = set(task["required_stages"])
    present_stages = {item["stage"] for item in normalized_checks}
    if normalized_acceptance is not None and not required_stages.issubset(present_stages):
        missing = sorted(required_stages - present_stages)
        raise AgentEvalError(f"accepted/completed attempt is missing required stages: {missing}")

    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": raw["suite_id"],
        "task_id": raw["task_id"],
        "evaluation_run_id": raw["evaluation_run_id"],
        "agent_id": raw["agent_id"],
        "agent_config_id": raw["agent_config_id"],
        "actor": raw["actor"],
        "repository": raw["repository"],
        "baseline_ref": raw["baseline_ref"],
        "measurement_contract_ref": raw["measurement_contract_ref"],
        "started_at": raw["started_at"],
        "checks": sorted(normalized_checks, key=lambda item: (_parse_time(item["observed_at"], "observed_at"), item["stage"], item["attempt"])),
        "repairs": sorted(normalized_repairs, key=lambda item: _parse_time(item["observed_at"], "observed_at")),
        "human_interventions": sorted(normalized_interventions, key=lambda item: _parse_time(item["observed_at"], "observed_at")),
        "acceptance": normalized_acceptance,
        "measured_cost": normalized_cost,
        "evidence_refs": sorted(set(raw["evidence_refs"])),
    }


def _base_metadata(attempt: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent_eval": {
            "suite_id": attempt["suite_id"],
            "task_id": attempt["task_id"],
            "evaluation_run_id": attempt["evaluation_run_id"],
            "agent_id": attempt["agent_id"],
            "agent_config_id": attempt["agent_config_id"],
            "baseline_ref": attempt["baseline_ref"],
            "measurement_contract_ref": attempt["measurement_contract_ref"],
        }
    }


def attempt_to_events(attempt: dict[str, Any], suite: dict[str, Any]) -> list[dict[str, Any]]:
    value = validate_attempt(attempt, suite)
    metadata = _base_metadata(value)
    if value["measured_cost"] is not None:
        metadata = json.loads(json.dumps(metadata))
        metadata["agent_eval"]["measured_cost"] = value["measured_cost"]

    entity = f"agent-eval:{value['evaluation_run_id']}:{value['task_id']}"
    events: list[dict[str, Any]] = [
        {
            "observed_at": value["started_at"],
            "run_id": value["evaluation_run_id"],
            "project": "portfolio",
            "repository": value["repository"],
            "entity_type": "issue",
            "entity_id": entity,
            "from_state": None,
            "to_state": "IMPLEMENTING",
            "transition_class": "ADVANCE",
            "actor": value["actor"],
            "evidence_refs": value["evidence_refs"],
            "metadata": {**metadata, "agent_eval_signal": "START"},
        }
    ]

    previous_by_stage: dict[str, str | None] = {}
    for check in value["checks"]:
        outcome_state = "GREEN" if check["outcome"] == "PASS" else "FAILED"
        check_metadata = json.loads(json.dumps(metadata))
        check_metadata["agent_eval_signal"] = "CHECK"
        check_metadata["stage"] = check["stage"]
        check_metadata["attempt_number"] = check["attempt"]
        events.append(
            {
                "observed_at": check["observed_at"],
                "run_id": value["evaluation_run_id"],
                "project": "portfolio",
                "repository": value["repository"],
                "entity_type": "ci",
                "entity_id": f"{entity}:{check['stage'].lower()}",
                "from_state": previous_by_stage.get(check["stage"]),
                "to_state": outcome_state,
                "transition_class": "PROMOTION" if check["outcome"] == "PASS" else "FAILURE",
                "actor": value["actor"],
                "evidence_refs": check["evidence_refs"],
                "metadata": check_metadata,
            }
        )
        previous_by_stage[check["stage"]] = outcome_state

    for repair in value["repairs"]:
        repair_metadata = json.loads(json.dumps(metadata))
        repair_metadata["agent_eval_signal"] = "REPAIR"
        repair_metadata["repair_attempt"] = repair["attempt"]
        repair_metadata["failure_class"] = repair["failure_class"]
        events.append(
            {
                "observed_at": repair["observed_at"],
                "run_id": value["evaluation_run_id"],
                "project": "portfolio",
                "repository": value["repository"],
                "entity_type": "issue",
                "entity_id": entity,
                "from_state": "IMPLEMENTING",
                "to_state": f"REPAIR_{repair['attempt']}",
                "transition_class": "REPAIR",
                "actor": value["actor"],
                "evidence_refs": repair["evidence_refs"],
                "metadata": repair_metadata,
            }
        )

    for index, intervention in enumerate(value["human_interventions"], start=1):
        intervention_metadata = json.loads(json.dumps(metadata))
        intervention_metadata["agent_eval_signal"] = "HUMAN_INTERVENTION"
        intervention_metadata["reason"] = intervention["reason"]
        events.append(
            {
                "observed_at": intervention["observed_at"],
                "run_id": value["evaluation_run_id"],
                "project": "portfolio",
                "repository": value["repository"],
                "entity_type": "issue",
                "entity_id": entity,
                "from_state": "IMPLEMENTING",
                "to_state": f"HUMAN_INTERVENTION_{index}",
                "transition_class": "HUMAN_INTERVENTION",
                "actor": "HUMAN",
                "evidence_refs": intervention["evidence_refs"],
                "metadata": intervention_metadata,
            }
        )

    if value["acceptance"] is not None:
        acceptance = value["acceptance"]
        acceptance_metadata = json.loads(json.dumps(metadata))
        acceptance_metadata["agent_eval_signal"] = "ACCEPTANCE"
        events.append(
            {
                "observed_at": acceptance["observed_at"],
                "run_id": value["evaluation_run_id"],
                "project": "portfolio",
                "repository": value["repository"],
                "entity_type": "issue",
                "entity_id": entity,
                "from_state": "IMPLEMENTING",
                "to_state": "ACCEPTED" if acceptance["outcome"] == "PASS" else "REJECTED",
                "transition_class": "ADVANCE" if acceptance["outcome"] == "PASS" else "FAILURE",
                "actor": value["actor"],
                "evidence_refs": acceptance["evidence_refs"],
                "metadata": acceptance_metadata,
            }
        )

    return [validate_event(event) for event in events]


def record_attempt(
    attempt: dict[str, Any],
    suite: dict[str, Any],
    *,
    ledger: str | Path = DEFAULT_LEDGER,
) -> list[dict[str, Any]]:
    events = attempt_to_events(attempt, suite)
    return [append_event(ledger, event) for event in events]


def _metric_unknown(reason: str, unit: str) -> dict[str, Any]:
    return {"status": "UNKNOWN", "value": None, "unit": unit, "sample_count": 0, "reason": reason}


def _metric(value: float | int, sample_count: int, unit: str, refs: Iterable[str]) -> dict[str, Any]:
    return {
        "status": "MEASURED",
        "value": value,
        "unit": unit,
        "sample_count": sample_count,
        "evidence_refs": sorted(set(refs)),
    }


def _event_eval_meta(event: dict[str, Any]) -> dict[str, Any] | None:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("agent_eval")
    return value if isinstance(value, dict) else None


def _attempt_key(event: dict[str, Any]) -> tuple[str, str]:
    meta = _event_eval_meta(event)
    assert meta is not None
    return str(meta["evaluation_run_id"]), str(meta["task_id"])


def build_agent_eval_report(
    events: list[dict[str, Any]],
    suite: dict[str, Any],
    *,
    start: str,
    end: str,
) -> dict[str, Any]:
    suite = validate_suite(suite)
    start_dt = _parse_time(start, "start")
    end_dt = _parse_time(end, "end")
    if end_dt <= start_dt:
        raise AgentEvalError("report end must be after start")

    normalized = [validate_event(event) for event in events]
    scoped = []
    for event in normalized:
        meta = _event_eval_meta(event)
        if meta is None or meta.get("suite_id") != suite["suite_id"]:
            continue
        observed = _parse_time(event["observed_at"], "observed_at")
        if start_dt < observed <= end_dt:
            scoped.append(event)

    attempts: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in scoped:
        attempts[_attempt_key(event)].append(event)

    task_ids = {task["task_id"] for task in suite["tasks"]}
    records: list[dict[str, Any]] = []
    for key, attempt_events in attempts.items():
        attempt_events.sort(key=lambda item: _parse_time(item["observed_at"], "observed_at"))
        metas = [_event_eval_meta(event) for event in attempt_events]
        if any(meta is None for meta in metas):
            continue
        meta0 = metas[0]
        assert meta0 is not None
        if meta0.get("task_id") not in task_ids:
            continue
        invariant_fields = (
            "suite_id",
            "task_id",
            "evaluation_run_id",
            "agent_id",
            "agent_config_id",
            "baseline_ref",
            "measurement_contract_ref",
        )
        for meta in metas[1:]:
            assert meta is not None
            if any(meta.get(field) != meta0.get(field) for field in invariant_fields):
                raise AgentEvalError(f"inconsistent agent-eval metadata for attempt {key}")

        starts = [event for event in attempt_events if event.get("metadata", {}).get("agent_eval_signal") == "START"]
        if len(starts) != 1:
            raise AgentEvalError(f"attempt {key} must contain exactly one START event")
        start_event = starts[0]

        checks: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in attempt_events:
            metadata = event.get("metadata", {})
            if metadata.get("agent_eval_signal") == "CHECK":
                checks[str(metadata.get("stage"))].append(event)
        for stage in checks:
            checks[stage].sort(
                key=lambda event: (
                    int(event.get("metadata", {}).get("attempt_number", 0)),
                    _parse_time(event["observed_at"], "observed_at"),
                )
            )

        acceptances = [
            event for event in attempt_events if event.get("metadata", {}).get("agent_eval_signal") == "ACCEPTANCE"
        ]
        if len(acceptances) > 1:
            raise AgentEvalError(f"attempt {key} has multiple acceptance events")
        acceptance_event = acceptances[0] if acceptances else None

        repairs = [event for event in attempt_events if event["transition_class"] == "REPAIR"]
        interventions = [
            event for event in attempt_events if event["transition_class"] == "HUMAN_INTERVENTION"
        ]
        failure_classes = [
            str(event.get("metadata", {}).get("failure_class"))
            for event in repairs
            if _nonempty(event.get("metadata", {}).get("failure_class"))
        ]

        cost = meta0.get("measured_cost")
        cost_value = None
        cost_currency = None
        cost_refs: list[str] = []
        if isinstance(cost, dict):
            value = cost.get("value")
            if not isinstance(value, bool) and isinstance(value, (int, float)) and value >= 0 and _nonempty(cost.get("currency")):
                cost_value = float(value)
                cost_currency = str(cost["currency"])
                refs = cost.get("evidence_refs", [])
                if isinstance(refs, list):
                    cost_refs = [str(item) for item in refs if _nonempty(item)]

        elapsed = None
        if acceptance_event is not None:
            elapsed = (
                _parse_time(acceptance_event["observed_at"], "observed_at")
                - _parse_time(start_event["observed_at"], "observed_at")
            ).total_seconds()
            if elapsed < 0:
                raise AgentEvalError(f"attempt {key} acceptance predates start")

        records.append(
            {
                "evaluation_run_id": meta0["evaluation_run_id"],
                "task_id": meta0["task_id"],
                "agent_id": meta0["agent_id"],
                "agent_config_id": meta0["agent_config_id"],
                "baseline_ref": meta0["baseline_ref"],
                "measurement_contract_ref": meta0["measurement_contract_ref"],
                "first_pass_preflight": (
                    checks["PREFLIGHT"][0]["to_state"] == "GREEN" if checks.get("PREFLIGHT") else None
                ),
                "first_pass_ci": (
                    checks["PR_CI"][0]["to_state"] == "GREEN" if checks.get("PR_CI") else None
                ),
                "repair_iterations": len(repairs),
                "human_intervention_count": len(interventions),
                "accepted": (
                    acceptance_event["to_state"] == "ACCEPTED" if acceptance_event is not None else None
                ),
                "elapsed_seconds": elapsed,
                "failure_classes": failure_classes,
                "measured_cost_value": cost_value,
                "measured_cost_currency": cost_currency,
                "measured_cost_refs": cost_refs,
                "evidence_refs": sorted(
                    {
                        ref
                        for event in attempt_events
                        for ref in event["evidence_refs"]
                    }
                ),
            }
        )

    cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        cohorts[str(record["baseline_ref"])].append(record)

    cohort_reports: list[dict[str, Any]] = []
    for baseline_ref in sorted(cohorts):
        by_agent: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for record in cohorts[baseline_ref]:
            by_agent[(str(record["agent_id"]), str(record["agent_config_id"]))].append(record)
        agent_reports = []
        task_coverage_sets: list[set[str]] = []
        for (agent_id, config_id), group in sorted(by_agent.items()):
            refs = [ref for item in group for ref in item["evidence_refs"]]
            coverage = {str(item["task_id"]) for item in group}
            task_coverage_sets.append(coverage)
            accepted_values = [item["accepted"] for item in group if item["accepted"] is not None]
            preflight_values = [
                item["first_pass_preflight"] for item in group if item["first_pass_preflight"] is not None
            ]
            ci_values = [item["first_pass_ci"] for item in group if item["first_pass_ci"] is not None]
            elapsed_values = [
                float(item["elapsed_seconds"]) for item in group if item["elapsed_seconds"] is not None
            ]
            repair_values = [int(item["repair_iterations"]) for item in group]
            intervention_values = [int(item["human_intervention_count"]) for item in group]
            failures = Counter(
                failure
                for item in group
                for failure in item["failure_classes"]
            )
            costs_by_currency: dict[str, list[tuple[float, list[str]]]] = defaultdict(list)
            for item in group:
                if item["measured_cost_value"] is not None and item["measured_cost_currency"] is not None:
                    costs_by_currency[str(item["measured_cost_currency"])].append(
                        (float(item["measured_cost_value"]), list(item["measured_cost_refs"]))
                    )

            agent_reports.append(
                {
                    "agent_id": agent_id,
                    "agent_config_id": config_id,
                    "task_coverage": sorted(coverage),
                    "attempt_count": len(group),
                    "deterministic_task_pass_rate": (
                        _metric(
                            sum(1 for value in accepted_values if value) / len(accepted_values),
                            len(accepted_values),
                            "ratio",
                            refs,
                        )
                        if accepted_values
                        else _metric_unknown("no deterministic acceptance events", "ratio")
                    ),
                    "first_pass_preflight_rate": (
                        _metric(
                            sum(1 for value in preflight_values if value) / len(preflight_values),
                            len(preflight_values),
                            "ratio",
                            refs,
                        )
                        if preflight_values
                        else _metric_unknown("no preflight evidence", "ratio")
                    ),
                    "first_pass_ci_rate": (
                        _metric(
                            sum(1 for value in ci_values if value) / len(ci_values),
                            len(ci_values),
                            "ratio",
                            refs,
                        )
                        if ci_values
                        else _metric_unknown("no PR CI evidence", "ratio")
                    ),
                    "median_repair_iterations": (
                        _metric(statistics.median(repair_values), len(repair_values), "iterations", refs)
                        if repair_values
                        else _metric_unknown("no attempts", "iterations")
                    ),
                    "human_intervention_rate": (
                        _metric(
                            sum(1 for value in intervention_values if value > 0) / len(intervention_values),
                            len(intervention_values),
                            "ratio",
                            refs,
                        )
                        if intervention_values
                        else _metric_unknown("no attempts", "ratio")
                    ),
                    "median_elapsed_seconds": (
                        _metric(statistics.median(elapsed_values), len(elapsed_values), "seconds", refs)
                        if elapsed_values
                        else _metric_unknown("no completed deterministic acceptance events", "seconds")
                    ),
                    "repeated_failure_classes": {
                        failure_class: count
                        for failure_class, count in sorted(failures.items())
                        if count >= 2
                    },
                    "measured_cost": (
                        {
                            currency: _metric(
                                sum(value for value, _ in values),
                                len(values),
                                currency,
                                [ref for _, cost_refs in values for ref in cost_refs],
                            )
                            for currency, values in sorted(costs_by_currency.items())
                        }
                        if costs_by_currency
                        else {"status": "UNKNOWN", "reason": "no measured cost evidence"}
                    ),
                }
            )

        comparable_task_coverage = (
            len(agent_reports) >= 2
            and bool(task_coverage_sets)
            and all(coverage == task_coverage_sets[0] for coverage in task_coverage_sets[1:])
        )
        cohort_reports.append(
            {
                "baseline_ref": baseline_ref,
                "comparison_status": (
                    "COMPARABLE"
                    if comparable_task_coverage
                    else "INSUFFICIENT_DATA"
                ),
                "comparison_reason": (
                    "all agent configurations cover the same task set under one suite/baseline"
                    if comparable_task_coverage
                    else "need at least two agent configurations with identical task coverage under this suite/baseline"
                ),
                "agents": agent_reports,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": suite["suite_id"],
        "start": start,
        "end": end,
        "attempt_count": len(records),
        "cohorts": cohort_reports,
        "status": "MEASURED" if records else "INSUFFICIENT_DATA",
        "advisory_only": True,
        "product_reality_inferred": False,
        "canonical_kpi_ledger": suite["canonical_kpi_ledger"],
    }


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AgentEvalError(f"{path} must contain a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-orchestrator agent-eval")
    sub = parser.add_subparsers(dest="command", required=True)

    validate_suite_cmd = sub.add_parser("validate-suite")
    validate_suite_cmd.add_argument("suite", nargs="?", type=Path, default=DEFAULT_SUITE)

    validate_attempt_cmd = sub.add_parser("validate-attempt")
    validate_attempt_cmd.add_argument("attempt", type=Path)
    validate_attempt_cmd.add_argument("--suite", type=Path, default=DEFAULT_SUITE)

    record_cmd = sub.add_parser("record")
    record_cmd.add_argument("attempt", type=Path)
    record_cmd.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    record_cmd.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)

    report_cmd = sub.add_parser("report")
    report_cmd.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    report_cmd.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    report_cmd.add_argument("--start", required=True)
    report_cmd.add_argument("--end", required=True)

    args = parser.parse_args(argv)

    try:
        suite = load_suite(args.suite)
        if args.command == "validate-suite":
            validate_suite_files(suite, Path.cwd())
            result = {
                "schema_version": SCHEMA_VERSION,
                "status": "GREEN",
                "suite_id": suite["suite_id"],
                "task_count": len(suite["tasks"]),
                "advisory_only": True,
                "product_reality_inferred": False,
            }
        elif args.command == "validate-attempt":
            attempt = validate_attempt(_load_json(args.attempt), suite)
            result = {
                "schema_version": SCHEMA_VERSION,
                "status": "GREEN",
                "suite_id": attempt["suite_id"],
                "task_id": attempt["task_id"],
                "evaluation_run_id": attempt["evaluation_run_id"],
            }
        elif args.command == "record":
            events = record_attempt(_load_json(args.attempt), suite, ledger=args.ledger)
            result = {
                "schema_version": SCHEMA_VERSION,
                "status": "RECORDED",
                "event_count": len(events),
                "event_ids": [event["event_id"] for event in events],
                "ledger": str(args.ledger),
            }
        else:
            result = build_agent_eval_report(
                load_events(args.ledger),
                suite,
                start=args.start,
                end=args.end,
            )
    except (AgentEvalError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "RED", "error": str(exc)}, indent=2, sort_keys=True))
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
