"""Pure, versioned denial-ledger serialization for offline task selection.

No I/O or tool invocation occurs here. Callers are responsible for durable
storage and must not reinterpret an existing denial as retry permission.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from project_orchestrator.development_execution_policy import Denial

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DenialLedger:
    denials: tuple[Denial, ...]

    @classmethod
    def parse(cls, payload: Mapping[str, object]) -> "DenialLedger":
        if not isinstance(payload, Mapping) or payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("DENIAL_LEDGER_SCHEMA_UNVERIFIED")
        rows = payload.get("denials")
        if not isinstance(rows, list):
            raise ValueError("DENIAL_LEDGER_ROWS_UNVERIFIED")
        result = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("DENIAL_LEDGER_ROW_UNVERIFIED")
            operation_id = row.get("operation_id")
            reason = row.get("reason")
            if (
                not isinstance(operation_id, str)
                or not operation_id.strip()
                or not isinstance(reason, str)
                or not reason.strip()
                or operation_id in seen
            ):
                raise ValueError("DENIAL_LEDGER_IDENTITY_UNVERIFIED")
            seen.add(operation_id)
            result.append(Denial(operation_id, reason))
        return cls(tuple(result))

    def record(self, operation_id: str, reason: str) -> "DenialLedger":
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("DENIAL_OPERATION_ID_UNVERIFIED")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("DENIAL_REASON_UNVERIFIED")
        if any(entry.operation_id == operation_id for entry in self.denials):
            return self
        return DenialLedger(self.denials + (Denial(operation_id, reason),))

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "denials": [
                {"operation_id": item.operation_id, "reason": item.reason}
                for item in self.denials
            ],
        }
