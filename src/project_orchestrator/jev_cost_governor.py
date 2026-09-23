from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def decision_fingerprint(request: dict[str, Any]) -> str:
    payload = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_governor_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "seen": {}, "calls": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Jev governor state must be an object")
    data.setdefault("seen", {})
    data.setdefault("calls", 0)
    return data


def admission(
    request: dict[str, Any],
    *,
    state: dict[str, Any],
    free_credit_available: bool,
) -> dict[str, Any]:
    fingerprint = decision_fingerprint(request)
    if fingerprint in state.get("seen", {}):
        return {
            "admitted": False,
            "reason": "CACHED_IDENTICAL_DECISION",
            "fingerprint": fingerprint,
            "cached": state["seen"][fingerprint],
            "paid_spend_authorized": False,
        }
    if not free_credit_available:
        return {
            "admitted": False,
            "reason": "FREE_CREDIT_UNAVAILABLE",
            "fingerprint": fingerprint,
            "cached": None,
            "paid_spend_authorized": False,
        }
    return {
        "admitted": True,
        "reason": "FREE_CREDIT_ONLY",
        "fingerprint": fingerprint,
        "cached": None,
        "paid_spend_authorized": False,
    }


def record_free_result(
    state: dict[str, Any],
    *,
    fingerprint: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    updated = json.loads(json.dumps(state))
    updated.setdefault("seen", {})[fingerprint] = result
    updated["calls"] = int(updated.get("calls", 0)) + 1
    updated["schema_version"] = SCHEMA_VERSION
    return updated


def save_governor_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
