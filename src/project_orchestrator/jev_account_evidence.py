from __future__ import annotations

from typing import Any

from .jev_readiness import assess_free_readiness


PUBLIC_API_CAPABILITIES = {
    "systemone": "POST /v1/systemone",
    "models": "GET /v1/models",
    "credit_balance_endpoint": None,
    "billing_settings_endpoint": None,
}


def collect_from_public_api_discovery() -> dict[str, Any]:
    """Describe only facts available from TypeSafe's documented public API surface.

    Current public API documentation exposes inference and model discovery, but no
    documented credit-balance or billing-settings endpoint. This collector must
    therefore fail closed rather than manufacture account evidence.
    """
    return {
        "schema_version": 1,
        "source": "TYPESAFE_PUBLIC_API_DISCOVERY",
        "public_api": dict(PUBLIC_API_CAPABILITIES),
        "promotional_credit_available": None,
        "purchased_credit_required": None,
        "automatic_refill_enabled": None,
        "authoritative_for_free_readiness": False,
        "network_call_required": False,
        "paid_spend_authorized": False,
    }


def readiness_from_discovery(*, env: dict[str, str] | None = None) -> dict[str, Any]:
    evidence = collect_from_public_api_discovery()
    readiness = assess_free_readiness(evidence, env=env)
    return {
        "schema_version": 1,
        "account_evidence": evidence,
        "readiness": readiness,
        "next_action": "OBTAIN_ACCOUNT_EXPORT_OR_PROVIDER_ACCOUNT_ENDPOINT",
    }
