#!/usr/bin/env python3
"""Fail-closed gate between readiness evidence and autonomous Evolution runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def validate_activation(state: dict[str, Any], expected_heads: dict[str, str]) -> tuple[bool, str]:
    if state.get("enabled") is not True:
        return False, "ACTIVATION_DISABLED"
    if state.get("reason") != "READINESS_PASS":
        return False, "ACTIVATION_REASON_NOT_GREEN"
    heads = state.get("heads")
    if not isinstance(heads, dict) or not heads:
        return False, "ACTIVATION_HEADS_MISSING"
    if heads != expected_heads:
        return False, "ACTIVATION_HEADS_STALE"
    return True, "ACTIVATION_CURRENT"


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
    print(json.dumps({"allowed": allowed, "reason": reason}, sort_keys=True))
    return 0 if allowed else 1


if __name__ == "__main__":
    sys.exit(main())
