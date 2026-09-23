from __future__ import annotations

import os
from typing import Any


def assess_free_readiness(
    account_evidence: dict[str, Any] | None,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fail-closed readiness gate for zero-dollar live Jev activation."""
    environment = os.environ if env is None else env
    evidence = account_evidence if isinstance(account_evidence, dict) else {}

    key_present = bool(environment.get("TYPESAFE_API_KEY"))
    live_enabled = environment.get("JEV_SHADOW_LIVE") == "1"

    source = evidence.get("source")
    promotional = evidence.get("promotional_credit_available")
    purchased_required = evidence.get("purchased_credit_required")
    auto_refill = evidence.get("automatic_refill_enabled")

    authoritative = source in {"TYPESAFE_ACCOUNT_API", "TYPESAFE_ACCOUNT_EXPORT"}
    free_confirmed = authoritative and promotional is True and purchased_required is False
    refill_safe = auto_refill is False

    reasons = []
    if not key_present:
        reasons.append("TYPESAFE_API_KEY_MISSING")
    if not live_enabled:
        reasons.append("JEV_SHADOW_LIVE_DISABLED")
    if not authoritative:
        reasons.append("AUTHORITATIVE_ACCOUNT_EVIDENCE_MISSING")
    if promotional is not True:
        reasons.append("PROMOTIONAL_CREDIT_NOT_CONFIRMED")
    if purchased_required is not False:
        reasons.append("PURCHASED_CREDIT_REQUIREMENT_NOT_EXCLUDED")
    if auto_refill is not False:
        reasons.append("AUTOMATIC_REFILL_NOT_CONFIRMED_OFF")

    ready = key_present and live_enabled and free_confirmed and refill_safe
    return {
        "schema_version": 1,
        "mode": "ZERO_DOLLAR_READINESS",
        "ready_for_free_live_shadow": ready,
        "free_credit_available": ready,
        "credential_present": key_present,
        "live_enabled": live_enabled,
        "account_evidence_authoritative": authoritative,
        "automatic_refill_confirmed_off": refill_safe,
        "reasons": reasons,
        "paid_spend_authorized": False,
        "account_evidence_persisted": False,
    }
