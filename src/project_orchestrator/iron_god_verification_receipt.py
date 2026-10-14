"""Verification Receipt schema: a validated, immutable record of one
verification run's evidence.

This module defines and validates the *shape* of a verification receipt.
It does not run any verification itself -- the existing
engineering/preflight.py's preflight-evidence.json is the running
example of the kind of evidence this schema formalizes: an exact head
SHA, the set of gates run, and a pass/fail per gate, with a content hash
that lets a receipt be checked for tampering after the fact (the same
idea local_continuous_worker.py already applies informally via its own
receipt_sha256 handling, formalized here as a reusable schema).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

REQUIRED_RECEIPT_FIELDS = ("head_sha", "repository", "gates", "content_hash")
REQUIRED_GATE_FIELDS = ("name", "returncode", "timed_out", "interrupted")

GREEN = "GREEN"
RED = "RED"


class ReceiptValidationError(RuntimeError):
    """Raised when a receipt fails validation; callers must treat the
    verification as unproven, never as a passing result."""


@dataclass(frozen=True)
class GateResult:
    name: str
    returncode: int
    timed_out: bool
    interrupted: bool

    @property
    def passed(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.interrupted


@dataclass(frozen=True)
class VerificationReceipt:
    head_sha: str
    repository: str
    gates: tuple[GateResult, ...]
    content_hash: str

    @property
    def status(self) -> str:
        if not self.gates:
            return RED
        return GREEN if all(gate.passed for gate in self.gates) else RED


def compute_content_hash(
    head_sha: str,
    repository: str,
    gates: tuple[GateResult, ...],
) -> str:
    """A stable hash over the receipt's own evidence, used to detect
    tampering after the fact."""
    payload = {
        "head_sha": head_sha,
        "repository": repository,
        "gates": [
            {
                "name": gate.name,
                "returncode": gate.returncode,
                "timed_out": gate.timed_out,
                "interrupted": gate.interrupted,
            }
            for gate in sorted(gates, key=lambda g: g.name)
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_receipt(
    head_sha: str,
    repository: str,
    gates: tuple[GateResult, ...],
) -> VerificationReceipt:
    """Construct a receipt with a freshly-computed, correct content hash."""
    content_hash = compute_content_hash(head_sha, repository, gates)
    return VerificationReceipt(
        head_sha=head_sha,
        repository=repository,
        gates=gates,
        content_hash=content_hash,
    )


def validate_receipt(payload: dict) -> VerificationReceipt:
    """Parse and validate a raw (e.g. JSON-decoded) receipt payload.

    Fails closed: any missing field, malformed gate entry, duplicate gate
    name, or content-hash mismatch raises ReceiptValidationError rather
    than returning a best-effort receipt.
    """
    for field_name in REQUIRED_RECEIPT_FIELDS:
        if field_name not in payload:
            raise ReceiptValidationError(
                f"receipt is missing required field: {field_name!r}"
            )

    raw_gates = payload["gates"]
    if not isinstance(raw_gates, list) or not raw_gates:
        raise ReceiptValidationError("receipt must have a non-empty list of gates")

    gates: list[GateResult] = []
    for raw_gate in raw_gates:
        if not isinstance(raw_gate, dict):
            raise ReceiptValidationError("each gate entry must be an object")
        for gate_field in REQUIRED_GATE_FIELDS:
            if gate_field not in raw_gate:
                raise ReceiptValidationError(
                    f"gate entry is missing required field: {gate_field!r}"
                )
        gates.append(
            GateResult(
                name=raw_gate["name"],
                returncode=raw_gate["returncode"],
                timed_out=raw_gate["timed_out"],
                interrupted=raw_gate["interrupted"],
            )
        )

    names = [gate.name for gate in gates]
    if len(names) != len(set(names)):
        raise ReceiptValidationError("receipt contains duplicate gate names")

    expected_hash = compute_content_hash(
        payload["head_sha"], payload["repository"], tuple(gates)
    )
    if payload["content_hash"] != expected_hash:
        raise ReceiptValidationError(
            f"content_hash mismatch: recorded {payload['content_hash']!r} does not "
            f"match recomputed {expected_hash!r}; receipt may have been tampered with"
        )

    return VerificationReceipt(
        head_sha=payload["head_sha"],
        repository=payload["repository"],
        gates=tuple(gates),
        content_hash=payload["content_hash"],
    )
