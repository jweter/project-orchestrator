from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_ALLOWED_STATES = {"PROPOSED", "BASELINE_READY", "ACTIVE", "MEASURING", "KEEP", "REJECT", "INSUFFICIENT_DATA"}
_TERMINAL = {"KEEP", "REJECT", "INSUFFICIENT_DATA"}


def empty_store() -> dict[str, Any]:
    return {"schema_version": 1, "experiments": []}


def load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_store()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("experiments"), list):
        raise ValueError("invalid process experiment store")
    return data


def write_store(path: Path, store: dict[str, Any]) -> None:
    if store.get("schema_version") != 1 or not isinstance(store.get("experiments"), list):
        raise ValueError("invalid process experiment store")
    path.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")



def replace_experiment(store: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    experiment_id = experiment.get("experiment_id")
    if not experiment_id:
        raise ValueError("experiment_id is required")
    found = False
    updated = []
    for item in store.get("experiments", []):
        if item.get("experiment_id") == experiment_id:
            updated.append(experiment)
            found = True
        else:
            updated.append(item)
    if not found:
        raise ValueError(f"unknown experiment_id: {experiment_id}")
    return {"schema_version": 1, "experiments": updated}


def build_experiment(proposal: dict[str, Any], *, target_metrics: list[str], baseline_refs: list[str]) -> dict[str, Any]:
    required = {"repository", "friction_type", "evidence_refs", "proposed_mechanism", "verification_plan", "safety_boundaries"}
    missing = required - proposal.keys()
    if missing:
        raise ValueError("proposal missing fields: " + ", ".join(sorted(missing)))
    if not target_metrics or not all(isinstance(x, str) and x.strip() for x in target_metrics):
        raise ValueError("target_metrics must contain non-empty metric names")
    if not baseline_refs or not all(isinstance(x, str) and x.strip() for x in baseline_refs):
        raise ValueError("baseline_refs must contain measured evidence references")
    evidence_refs = sorted(set(proposal["evidence_refs"]))
    if len(evidence_refs) < 2:
        raise ValueError("process experiments require recurring verified proposal evidence")
    seed = json.dumps([proposal["repository"], proposal["friction_type"], evidence_refs, sorted(set(target_metrics))], sort_keys=True)
    experiment_id = "pex-" + hashlib.sha256(seed.encode()).hexdigest()[:12]
    return {
        "experiment_id": experiment_id,
        "repository": proposal["repository"],
        "friction_type": proposal["friction_type"],
        "state": "BASELINE_READY",
        "proposal_evidence_refs": evidence_refs,
        "target_metrics": sorted(set(target_metrics)),
        "baseline_refs": sorted(set(baseline_refs)),
        "change_ref": None,
        "verification_refs": [],
        "post_change_refs": [],
        "safety_invariants": list(proposal["safety_boundaries"]),
        "decision": None,
        "decision_evidence": [],
        "authority": "GOVERNED_ENGINEERING_ONLY",
    }


def activate(experiment: dict[str, Any], *, change_ref: str, verification_refs: list[str]) -> dict[str, Any]:
    if experiment.get("state") != "BASELINE_READY":
        raise ValueError("only BASELINE_READY experiments may activate")
    if not isinstance(change_ref, str) or not change_ref.strip():
        raise ValueError("change_ref is required")
    if not verification_refs:
        raise ValueError("verification_refs are required before activation")
    updated = dict(experiment)
    updated.update({"state": "ACTIVE", "change_ref": change_ref, "verification_refs": sorted(set(verification_refs))})
    return updated


def evaluate(
    experiment: dict[str, Any],
    *,
    metric_results: dict[str, dict[str, Any]],
    post_change_refs: list[str],
    safety_invariants_passed: bool | None,
) -> dict[str, Any]:
    if experiment.get("state") not in {"ACTIVE", "MEASURING"}:
        raise ValueError("only ACTIVE or MEASURING experiments may be evaluated")
    if not post_change_refs:
        return _decision(experiment, "INSUFFICIENT_DATA", [], "post-change evidence is missing")
    if safety_invariants_passed is not True:
        if safety_invariants_passed is False:
            return _decision(experiment, "REJECT", post_change_refs, "a safety or quality invariant regressed")
        return _decision(experiment, "INSUFFICIENT_DATA", post_change_refs, "safety invariant evidence is UNKNOWN")
    target_metrics = experiment["target_metrics"]
    if any(metric not in metric_results for metric in target_metrics):
        return _decision(experiment, "INSUFFICIENT_DATA", post_change_refs, "one or more target metrics are missing")
    states = []
    for metric in target_metrics:
        result = metric_results[metric]
        if result.get("status") != "MEASURED":
            return _decision(experiment, "INSUFFICIENT_DATA", post_change_refs, f"{metric} is not MEASURED")
        outcome = result.get("outcome")
        if outcome not in {"IMPROVED", "UNCHANGED", "DEGRADED"}:
            raise ValueError(f"invalid measured outcome for {metric}")
        states.append(outcome)
    if "DEGRADED" in states:
        return _decision(experiment, "REJECT", post_change_refs, "a target metric degraded")
    if "IMPROVED" in states:
        return _decision(experiment, "KEEP", post_change_refs, "measured improvement with safety invariants preserved")
    return _decision(experiment, "REJECT", post_change_refs, "no measured improvement")


def _decision(experiment: dict[str, Any], state: str, refs: list[str], reason: str) -> dict[str, Any]:
    if state not in _TERMINAL:
        raise ValueError("invalid terminal state")
    updated = dict(experiment)
    updated.update({
        "state": state,
        "post_change_refs": sorted(set(refs)),
        "decision": reason,
        "decision_evidence": sorted(set(experiment.get("verification_refs", []) + refs)),
    })
    return updated


def add_experiment(store: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    if store.get("schema_version") != 1 or not isinstance(store.get("experiments"), list):
        raise ValueError("invalid process experiment store")
    if experiment.get("state") not in _ALLOWED_STATES:
        raise ValueError("invalid experiment state")
    if any(item.get("experiment_id") == experiment.get("experiment_id") for item in store["experiments"]):
        raise ValueError("duplicate experiment_id")
    return {"schema_version": 1, "experiments": [*store["experiments"], experiment]}
