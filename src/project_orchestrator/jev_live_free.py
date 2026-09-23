from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from .jev_cost_governor import admission, load_governor_state, record_free_result, save_governor_state
from .jev_shadow import build_shadow_request, evaluate_shadow


def evaluate_free_live_shadow(
    *,
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
    governor_path: Path,
    free_credit_available: bool,
    transport: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Call Jev only when the zero-dollar governor explicitly admits the request."""
    local = evaluate_shadow(state=state, candidates=candidates, live=False)
    if local["decision"]["state"] != "SHADOW_PENDING":
        return {**local, "cost_governor": {"provider_call": False, "reason": "NO_NEURAL_CALL_NEEDED"}}

    request = build_shadow_request(state=state, candidates=candidates)
    governor = load_governor_state(governor_path)
    gate = admission(request, state=governor, free_credit_available=free_credit_available)

    if not gate["admitted"]:
        cached = gate.get("cached")
        if isinstance(cached, dict):
            return {**cached, "cost_governor": {**gate, "provider_call": False}}
        return {
            **local,
            "decision": {
                **local["decision"],
                "state": "FREE_ONLY_BLOCKED",
                "reason": "Jev provider call denied by zero-dollar governor",
            },
            "cost_governor": {**gate, "provider_call": False},
        }

    if os.environ.get("JEV_SHADOW_LIVE") != "1":
        return {
            **local,
            "decision": {**local["decision"], "state": "LIVE_DISABLED"},
            "cost_governor": {**gate, "provider_call": False},
        }

    result = evaluate_shadow(
        state=state,
        candidates=candidates,
        live=True,
        transport=transport,
    )
    if result["decision"].get("neural_call_made") is True:
        governor = record_free_result(
            governor,
            fingerprint=gate["fingerprint"],
            result=result,
        )
        save_governor_state(governor_path, governor)
    return {**result, "cost_governor": {**gate, "provider_call": result["decision"].get("neural_call_made") is True}}
