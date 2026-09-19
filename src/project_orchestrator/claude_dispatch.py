from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

API_VERSION = "2023-06-01"
BETA = "experimental-cc-routine-2026-04-01"
ROUTINES = {
    "portfolio_builder": ("https://api.anthropic.com/v1/claude_code/routines/trig_016m1YeMU9qCeZaEPA5Y6ezH/fire", "CLAUDE_ROUTINE_PORTFOLIO_BUILDER_TOKEN"),
    "repair_rescue": ("https://api.anthropic.com/v1/claude_code/routines/trig_014bWbUvStgR1ZPDTirXBbMT/fire", "CLAUDE_ROUTINE_REPAIR_RESCUE_TOKEN"),
    "adversarial_reviewer": ("https://api.anthropic.com/v1/claude_code/routines/trig_01FfMws2xiomQN97kzWRCZBp/fire", "CLAUDE_ROUTINE_ADVERSARIAL_REVIEWER_TOKEN"),
    "deep_product": ("https://api.anthropic.com/v1/claude_code/routines/trig_01XD9qfwkgnZbbWLRfCdzzfj/fire", "CLAUDE_ROUTINE_DEEP_PRODUCT_TOKEN"),
}

@dataclass(frozen=True)
class DispatchRecord:
    dispatch_id: str
    routine: str
    repository: str
    task_key: str
    session_id: str
    session_url: str
    started_at: float
    status: str = "DISPATCHED"

def _dispatch_id(routine: str, repository: str, task_key: str) -> str:
    raw = f"{routine}|{repository}|{task_key}".encode()
    return hashlib.sha256(raw).hexdigest()[:20]

def dispatch(routine: str, repository: str, task_key: str, text: str, state_dir: Path, *, opener: Callable = urllib.request.urlopen) -> DispatchRecord:
    if routine not in ROUTINES:
        raise ValueError(f"unknown Claude routine: {routine}")
    url, env_name = ROUTINES[routine]
    token = os.environ.get(env_name)
    if not token:
        raise RuntimeError(f"missing required secret: {env_name}")
    did = _dispatch_id(routine, repository, task_key)
    state_dir.mkdir(parents=True, exist_ok=True)
    lease = state_dir / f"{did}.json"
    if lease.exists():
        prior = json.loads(lease.read_text(encoding="utf-8"))
        raise RuntimeError(f"duplicate Claude dispatch blocked: {prior.get('session_id', did)}")
    payload = json.dumps({"text": text}).encode()
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "anthropic-version": API_VERSION,
        "anthropic-beta": BETA,
        "Content-Type": "application/json",
    })
    with opener(req, timeout=30) as response:
        result = json.loads(response.read().decode())
    record = DispatchRecord(did, routine, repository, task_key, result["claude_code_session_id"], result["claude_code_session_url"], time.time())
    lease.write_text(json.dumps(asdict(record), sort_keys=True, indent=2), encoding="utf-8")
    return record

def record_outcome(state_dir: Path, dispatch_id: str, **metrics: object) -> dict:
    path = state_dir / f"{dispatch_id}.json"
    if not path.exists():
        raise FileNotFoundError(dispatch_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"status","completed_at","pr_url","commit_sha","verification","product_advancement","repair_iterations","human_intervention","cycle_time_seconds","notes"}
    unknown = set(metrics) - allowed
    if unknown:
        raise ValueError(f"unsupported outcome fields: {sorted(unknown)}")
    data.update(metrics)
    path.write_text(json.dumps(data, sort_keys=True, indent=2), encoding="utf-8")
    return data

def summarize_measurements(state_dir: Path) -> dict:
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in state_dir.glob("*.json")]
    completed = [r for r in rows if r.get("status") in {"PASS","FAIL","NO_OP"}]
    advanced = sum(bool(r.get("product_advancement")) for r in completed)
    interventions = sum(bool(r.get("human_intervention")) for r in completed)
    return {"dispatches": len(rows), "completed": len(completed), "advanced": advanced, "human_interventions": interventions,
            "advancement_rate": (advanced / len(completed)) if completed else None}


ROLE_ALLOWED_OUTCOMES = {
    "portfolio_builder": {"PRODUCT_PR", "NO_OP", "BLOCKED"},
    "repair_rescue": {"REPAIR_PR", "NO_OP", "BLOCKED"},
    "adversarial_reviewer": {"REVIEW_FINDING", "REPAIR_PR", "NO_OP", "BLOCKED"},
    "deep_product": {"PRODUCT_PR", "NO_OP", "BLOCKED"},
}

def ingest_session_result(state_dir: Path, dispatch_id: str, *, outcome_kind: str,
                          repository: str | None = None, pr_url: str | None = None,
                          verification: str | None = None, product_advancement: bool = False,
                          human_intervention: bool = False, notes: str = "") -> dict:
    """Normalize a completed Claude session into measured evidence and flag role drift."""
    path = state_dir / f"{dispatch_id}.json"
    if not path.exists():
        raise FileNotFoundError(dispatch_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    routine = data["routine"]
    if outcome_kind not in {"PRODUCT_PR","REPAIR_PR","REVIEW_FINDING","NO_OP","BLOCKED","FAIL"}:
        raise ValueError(f"unknown Claude outcome: {outcome_kind}")
    role_ok = outcome_kind in ROLE_ALLOWED_OUTCOMES[routine] or outcome_kind == "FAIL"
    completed_at = time.time()
    data.update({
        "status": "NO_OP" if role_ok and outcome_kind == "NO_OP" else ("PASS" if role_ok and outcome_kind not in {"FAIL","BLOCKED"} else "FAIL"),
        "completed_at": completed_at,
        "cycle_time_seconds": max(0.0, completed_at - float(data["started_at"])),
        "outcome_kind": outcome_kind,
        "role_adherence": role_ok,
        "repository": repository or data.get("repository"),
        "pr_url": pr_url,
        "verification": verification,
        "product_advancement": bool(product_advancement),
        "human_intervention": bool(human_intervention),
        "notes": notes,
    })
    path.write_text(json.dumps(data, sort_keys=True, indent=2), encoding="utf-8")
    return data

def specialist_scorecard(state_dir: Path) -> dict:
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in state_dir.glob("*.json")]
    score = {}
    for routine in ROUTINES:
        rr = [r for r in rows if r.get("routine") == routine and "completed_at" in r]
        if not rr:
            score[routine] = {"completed": 0}
            continue
        score[routine] = {
            "completed": len(rr),
            "role_adherence_rate": sum(r.get("role_adherence") is True for r in rr) / len(rr),
            "advancement_rate": sum(bool(r.get("product_advancement")) for r in rr) / len(rr),
            "human_intervention_rate": sum(bool(r.get("human_intervention")) for r in rr) / len(rr),
            "median_cycle_time_seconds": sorted(float(r.get("cycle_time_seconds", 0)) for r in rr)[len(rr)//2],
        }
    return score

def seed_external_dispatch(state_dir: Path, *, routine: str, repository: str, task_key: str,
                           session_id: str, session_url: str, started_at: float) -> DispatchRecord:
    """Import an already-fired routine so its result can enter the same measurement ledger."""
    if routine not in ROUTINES:
        raise ValueError(f"unknown Claude routine: {routine}")
    did = _dispatch_id(routine, repository, task_key)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{did}.json"
    if path.exists():
        return DispatchRecord(**{k: json.loads(path.read_text())[k] for k in DispatchRecord.__dataclass_fields__})
    record = DispatchRecord(did, routine, repository, task_key, session_id, session_url, started_at)
    path.write_text(json.dumps(asdict(record), sort_keys=True, indent=2), encoding="utf-8")
    return record


TASK_ROUTING = {
    "DETERMINISTIC_FAILURE": "repair_rescue",
    "CI_FAILURE": "repair_rescue",
    "QUALITY_FAILURE": "repair_rescue",
    "RECENT_CHANGE_REVIEW": "adversarial_reviewer",
    "DEEP_ARCHITECTURE": "deep_product",
    "ALGORITHMIC_COMPLEXITY": "deep_product",
    "CROSS_MODULE_CORRECTNESS": "deep_product",
    "PRODUCT_SLICE": "portfolio_builder",
}

def select_actor(task: dict, scorecard: dict | None = None) -> dict:
    """Choose the cheapest trustworthy actor; Claude is earned, never the default."""
    kind = str(task.get("kind", "")).upper()
    if task.get("requires_human") or task.get("reserved_human_boundary"):
        return {"actor": "HUMAN", "reason": "reserved human boundary", "routine": None}
    if task.get("local_capability_verified") and kind in {"DETERMINISTIC_FAILURE","CI_FAILURE","QUALITY_FAILURE","PRODUCT_SLICE"}:
        return {"actor": "LAPTOP_ACTOR", "reason": "verified local capability available", "routine": None}
    routine = TASK_ROUTING.get(kind)
    if routine is None:
        return {"actor": "LAPTOP_ACTOR", "reason": "no evidence Claude is required", "routine": None}
    stats = (scorecard or {}).get(routine, {})
    completed = int(stats.get("completed", 0) or 0)
    if completed >= 3:
        if float(stats.get("role_adherence_rate", 0)) < 0.8:
            return {"actor": "LAPTOP_ACTOR", "reason": "Claude specialist demoted for role drift", "routine": None}
        if float(stats.get("human_intervention_rate", 1)) > 0.5:
            return {"actor": "LAPTOP_ACTOR", "reason": "Claude specialist demoted for intervention cost", "routine": None}
    return {"actor": "CLAUDE_CLOUD", "reason": f"specialist match for {kind}", "routine": routine}

def dispatch_if_warranted(task: dict, state_dir: Path, *, scorecard: dict | None = None,
                          opener: Callable = urllib.request.urlopen) -> dict:
    decision = select_actor(task, scorecard)
    if decision["actor"] != "CLAUDE_CLOUD":
        return {"dispatched": False, **decision}
    required = ("repository", "task_key", "prompt")
    missing = [key for key in required if not task.get(key)]
    if missing:
        raise ValueError(f"Claude dispatch task missing: {missing}")
    rec = dispatch(decision["routine"], task["repository"], task["task_key"], task["prompt"], state_dir, opener=opener)
    return {"dispatched": True, **decision, "dispatch_id": rec.dispatch_id, "session_id": rec.session_id}
