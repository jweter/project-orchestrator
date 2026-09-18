from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_ALLOWED_SCOPES = {"repository", "portfolio"}


def empty_store() -> dict[str, Any]:
    return {"schema_version": 1, "rules": []}


def load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_store()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("rules"), list):
        raise ValueError("invalid retained learning store")
    return data


def write_store(path: Path, store: dict[str, Any]) -> None:
    if store.get("schema_version") != 1 or not isinstance(store.get("rules"), list):
        raise ValueError("invalid retained learning store")
    path.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def retain_from_experiment(
    experiment: dict[str, Any],
    *,
    mechanism: str,
    applicability: dict[str, Any],
    scope: str = "repository",
) -> dict[str, Any]:
    if experiment.get("state") != "KEEP":
        raise ValueError("only KEEP experiments may become retained rules")
    if scope not in _ALLOWED_SCOPES:
        raise ValueError("invalid retained rule scope")
    evidence = sorted(set(experiment.get("decision_evidence", [])))
    if not evidence:
        raise ValueError("retained rule requires decision evidence")
    if not mechanism.strip():
        raise ValueError("mechanism is required")
    if not applicability:
        raise ValueError("applicability is required")
    seed = json.dumps(
        [experiment["experiment_id"], experiment["repository"], mechanism, applicability, scope],
        sort_keys=True,
    )
    return {
        "rule_id": "rule-" + hashlib.sha256(seed.encode()).hexdigest()[:12],
        "source_experiment_id": experiment["experiment_id"],
        "repository": experiment["repository"],
        "scope": scope,
        "mechanism": mechanism,
        "applicability": applicability,
        "evidence_refs": evidence,
        "safety_invariants": list(experiment.get("safety_invariants", [])),
        "status": "VERIFIED_RETAINED",
        "authority": "ADVISORY_TO_SCHEDULER_NORMAL_GATES_REQUIRED",
    }


def add_rule(store: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    if store.get("schema_version") != 1 or not isinstance(store.get("rules"), list):
        raise ValueError("invalid retained learning store")
    if rule.get("status") != "VERIFIED_RETAINED":
        raise ValueError("only verified retained rules may be stored")
    if any(x.get("rule_id") == rule.get("rule_id") for x in store["rules"]):
        return store
    return {"schema_version": 1, "rules": [*store["rules"], rule]}


def applicable_rules(
    store: dict[str, Any],
    *,
    repository: str,
    changed_paths: list[str],
    failure_class: str | None = None,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for rule in store.get("rules", []):
        if rule.get("status") != "VERIFIED_RETAINED":
            continue
        if rule.get("scope") == "repository" and rule.get("repository") != repository:
            continue
        applicability = rule.get("applicability", {})
        prefixes = applicability.get("path_prefixes", [])
        failures = applicability.get("failure_classes", [])
        if prefixes and not any(any(path.startswith(prefix) for prefix in prefixes) for path in changed_paths):
            continue
        if failures and failure_class not in failures:
            continue
        matches.append(rule)
    return matches
