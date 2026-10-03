#!/usr/bin/env python3
"""Narrow independent guardian for Foundry supervisor liveness."""
from __future__ import annotations
import argparse, json, os, time, uuid
from datetime import UTC, datetime
from pathlib import Path

from project_orchestrator.iron_foreman import recovery_decision, prepare_reclamation
from project_orchestrator.recovery_manager import process_start_fingerprint
from project_orchestrator.state_io import atomic_json

def _load(path: Path) -> dict:
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else {}
    except (OSError,json.JSONDecodeError,TypeError):
        return {}


def guardian_heartbeat(state: Path, *, guardian_id: str, status: str, decision: dict) -> dict:
    payload={"schema_version":1,"guardian_id":guardian_id,"pid":os.getpid(),
             "status":status,"decision_status":decision.get("status"),
             "heartbeat_at_utc":datetime.now(UTC).isoformat()}
    atomic_json(state/"iron-foreman-guardian.json",payload)
    return payload


def recovery_rate_allowed(state: Path, *, now: datetime | None=None,
                          cooldown_seconds: int=300) -> bool:
    prior=_load(state/"iron-foreman-last-recovery.json")
    stamp=prior.get("attempted_at_utc")
    if not stamp:
        return True
    try:
        attempted=datetime.fromisoformat(stamp.replace("Z","+00:00"))
        if attempted.tzinfo is None: attempted=attempted.replace(tzinfo=UTC)
    except (TypeError,ValueError):
        return False
    current=now or datetime.now(UTC)
    return (current-attempted).total_seconds() >= cooldown_seconds


def guardian_iteration(state: Path, *, guardian_id: str,
                       confirmation_seconds: int=30, cooldown_seconds: int=300) -> dict:
    now=datetime.now(UTC)
    decision=recovery_decision(state,now=now,confirmation_seconds=confirmation_seconds)
    if decision.get("status")!="STALL_CONFIRMED":
        guardian_heartbeat(state,guardian_id=guardian_id,status="WATCHING",decision=decision)
        return decision
    if not recovery_rate_allowed(state,now=now,cooldown_seconds=cooldown_seconds):
        decision={**decision,"status":"RECOVERY_COOLDOWN"}
        guardian_heartbeat(state,guardian_id=guardian_id,status="COOLDOWN",decision=decision)
        return decision
    prepared=prepare_reclamation(state,fingerprint_fn=process_start_fingerprint,now=now)
    if not prepared.get("prepared"):
        guardian_heartbeat(state,guardian_id=guardian_id,status="RECOVERY_REFUSED",decision=prepared)
        return prepared
    receipt={"schema_version":1,"status":"FENCED_AWAITING_HANDOFF",
             "guardian_id":guardian_id,
             "process_instance_id":prepared["fence"]["process_instance_id"],
             "burst_id":prepared["fence"]["burst_id"],
             "attempted_at_utc":now.isoformat()}
    atomic_json(state/"iron-foreman-last-recovery.json",receipt)
    guardian_heartbeat(state,guardian_id=guardian_id,status="RECOVERING",decision=receipt)
    return receipt


def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--state-root",required=True)
    p.add_argument("--poll-seconds",type=float,default=5)
    p.add_argument("--confirmation-seconds",type=int,default=30)
    p.add_argument("--cooldown-seconds",type=int,default=300)
    p.add_argument("--once",action="store_true")
    a=p.parse_args(); state=Path(a.state_root); state.mkdir(parents=True,exist_ok=True)
    guardian_id=uuid.uuid4().hex
    while True:
        guardian_iteration(state,guardian_id=guardian_id,
                           confirmation_seconds=max(1,a.confirmation_seconds),
                           cooldown_seconds=max(1,a.cooldown_seconds))
        if a.once:return 0
        time.sleep(max(1,a.poll_seconds))


if __name__=="__main__":
    raise SystemExit(main())
