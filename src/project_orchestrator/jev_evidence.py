from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
TERMINAL_OUTCOMES = {"VERIFIED_ADVANCEMENT", "FAILED", "ABANDONED", "NO_ADVANCEMENT"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def decision_record(*, run_id: str, project_id: str, deterministic_choice: str | None,
                    shadow: dict[str, Any]) -> dict[str, Any]:
    decision = shadow.get("decision", {})
    legal = shadow.get("deterministic_legal_candidate_ids", [])
    if not isinstance(legal, list) or any(not isinstance(x, str) for x in legal):
        raise ValueError("shadow legal candidate ids must be strings")
    if deterministic_choice is not None and deterministic_choice not in legal:
        raise ValueError("deterministic choice must belong to legal candidate set")
    shadow_choice = decision.get("selected_candidate_id")
    if shadow_choice is not None and shadow_choice not in legal:
        raise ValueError("shadow choice must belong to legal candidate set")
    return {
        "schema_version": SCHEMA_VERSION,
        "recorded_at_utc": _now(),
        "run_id": run_id,
        "project_id": project_id,
        "legal_candidate_ids": list(legal),
        "deterministic_choice": deterministic_choice,
        "shadow_choice": shadow_choice,
        "shadow_state": decision.get("state", "UNKNOWN"),
        "probabilities": dict(decision.get("probabilities") or {}),
        "neural_call_made": bool(decision.get("neural_call_made", False)),
        "execution_authority": False,
        "outcome": None,
    }


def attach_outcome(record: dict[str, Any], *, candidate_id: str, outcome: str,
                   verification_ref: str | None = None, retries: int = 0,
                   verification_churn: int = 0) -> dict[str, Any]:
    if candidate_id not in record.get("legal_candidate_ids", []):
        raise ValueError("outcome candidate must belong to recorded legal set")
    if outcome not in TERMINAL_OUTCOMES:
        raise ValueError(f"unsupported outcome: {outcome}")
    updated = dict(record)
    updated["outcome"] = {
        "candidate_id": candidate_id,
        "state": outcome,
        "verification_ref": verification_ref,
        "retries": max(0, int(retries)),
        "verification_churn": max(0, int(verification_churn)),
        "recorded_at_utc": _now(),
    }
    return updated


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"line {line_no}: ledger record must be an object")
        records.append(value)
    return records


def evaluate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    comparable = []
    disagreement = 0
    shadow_wins = deterministic_wins = ties = 0
    by_project: Counter[str] = Counter()
    for record in records:
        outcome = record.get("outcome")
        if not isinstance(outcome, dict):
            continue
        deterministic = record.get("deterministic_choice")
        shadow = record.get("shadow_choice")
        winner = outcome.get("candidate_id") if outcome.get("state") == "VERIFIED_ADVANCEMENT" else None
        if not deterministic or not shadow:
            continue
        comparable.append(record)
        by_project[str(record.get("project_id", "unknown"))] += 1
        if deterministic != shadow:
            disagreement += 1
        if winner is None or deterministic == shadow == winner:
            ties += 1
        elif shadow == winner and deterministic != winner:
            shadow_wins += 1
        elif deterministic == winner and shadow != winner:
            deterministic_wins += 1
        else:
            ties += 1
    total = len(comparable)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "SHADOW_EVIDENCE_ONLY",
        "records_total": len(records),
        "comparable_decisions": total,
        "disagreements": disagreement,
        "disagreement_rate": (disagreement / total) if total else None,
        "shadow_better": shadow_wins,
        "deterministic_better": deterministic_wins,
        "ties_or_inconclusive": ties,
        "by_project": dict(sorted(by_project.items())),
        "promotion_authority": False,
        "promotion_ready": False,
        "note": "Evidence report is descriptive only; promotion requires a separately governed policy decision.",
    }


def replay(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for record in records:
        outcome = record.get("outcome")
        if not isinstance(outcome, dict):
            continue
        rows.append({
            "run_id": record.get("run_id"),
            "project_id": record.get("project_id"),
            "deterministic_choice": record.get("deterministic_choice"),
            "shadow_choice": record.get("shadow_choice"),
            "actual_candidate": outcome.get("candidate_id"),
            "actual_state": outcome.get("state"),
            "disagreed": record.get("deterministic_choice") != record.get("shadow_choice"),
            "shadow_matched_verified_advancement": (
                outcome.get("state") == "VERIFIED_ADVANCEMENT"
                and record.get("shadow_choice") == outcome.get("candidate_id")
            ),
        })
    return {"schema_version": SCHEMA_VERSION, "rows": rows, "summary": evaluate_records(records)}
