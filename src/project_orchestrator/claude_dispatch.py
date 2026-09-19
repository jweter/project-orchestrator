from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
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
