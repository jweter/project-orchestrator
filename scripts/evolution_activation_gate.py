#!/usr/bin/env python3
"""Fail-closed gate between readiness evidence and autonomous Evolution runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def validate_products(state: dict[str, Any], expected_heads: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Validate exact-head authority independently for each declared product."""
    results: dict[str, dict[str, Any]] = {}
    products = state.get("products")
    if not isinstance(products, dict):
        return results
    for name, product in products.items():
        if not isinstance(product, dict) or product.get("enabled") is not True:
            results[name] = {"allowed": False, "reason": "PRODUCT_DISABLED"}
            continue
        heads = product.get("repositories")
        if not isinstance(heads, dict) or not heads:
            results[name] = {"allowed": False, "reason": "PRODUCT_HEADS_MISSING"}
            continue
        current = {repo: expected_heads.get(repo) for repo in heads}
        if any(value is None for value in current.values()) or current != heads:
            results[name] = {"allowed": False, "reason": "PRODUCT_HEADS_STALE"}
            continue
        results[name] = {"allowed": True, "reason": "PRODUCT_CURRENT"}
    return results

def validate_activation(state: dict[str, Any], expected_heads: dict[str, str]) -> tuple[bool, str]:
    if state.get("reason") != "READINESS_PASS":
        return False, "ACTIVATION_REASON_NOT_GREEN"
    products = validate_products(state, expected_heads)
    if any(result["allowed"] for result in products.values()):
        return True, "ACTIVATION_CURRENT"
    return False, "NO_CURRENT_PRODUCTS"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--activation-state", required=True, type=Path)
    parser.add_argument("--expected-heads", required=True, type=Path)
    args = parser.parse_args()
    try:
        state = json.loads(args.activation_state.read_text(encoding="utf-8"))
        expected = json.loads(args.expected_heads.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"allowed": False, "reason": f"ACTIVATION_EVIDENCE_UNAVAILABLE: {exc}"}))
        return 2
    allowed, reason = validate_activation(state, expected)
    products = validate_products(state, expected)
    print(json.dumps({"allowed": allowed, "reason": reason, "products": products}, sort_keys=True))
    return 0 if allowed else 1


if __name__ == "__main__":
    sys.exit(main())
