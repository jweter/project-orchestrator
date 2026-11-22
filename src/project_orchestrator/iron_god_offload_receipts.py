"""Append-only verification references for the existing SQLite offload lane.

This is a local library, not an MCP write tool or a source of owner authority.
The caller's *separate* native verifier must authenticate the evidence bytes,
exact tested head, source product, and current live owner/worker ownership.
Recording here neither promotes development-memory rules nor authorizes merge.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from . import iron_god_offload as offload

_GATES = frozenset({"TARGETED_TESTS", "FULL_PREFLIGHT", "GITHUB_CI", "MERGE", "PRODUCT_REALITY"})
_RESULTS = frozenset({"PASS", "FAIL"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_REF = re.compile(r"^(?:native|preflight|github|product):[A-Za-z0-9._/-]{1,160}$")
_LIMIT = 30


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _safe_evidence_ref(ref: object) -> bool:
    """Opaque identifiers only: never paths, URLs or traversal components."""
    if not isinstance(ref, str) or _REF.fullmatch(ref) is None:
        return False
    suffix = ref.split(":", 1)[1]
    return all(part not in ("", ".", "..") for part in suffix.split("/"))


def _schema(db_path: Path) -> None:
    """Add a projection inside the existing coordination DB, never a new lease DB."""
    offload._schema(db_path)
    with closing(offload.coordination._connect(db_path)) as db:
        db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS offload_verification_receipts (
                receipt_id TEXT PRIMARY KEY,
                offer_id TEXT NOT NULL REFERENCES offload_candidates(offer_id),
                gate TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (offer_id, gate, evidence_ref)
            );
            CREATE TRIGGER IF NOT EXISTS offload_verification_receipts_no_update
              BEFORE UPDATE ON offload_verification_receipts
              BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_OFFLOAD_VERIFICATION'); END;
            CREATE TRIGGER IF NOT EXISTS offload_verification_receipts_no_delete
              BEFORE DELETE ON offload_verification_receipts
              BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_OFFLOAD_VERIFICATION'); END;
            COMMIT;
        """)


def record_independently_checked_evidence(
    db_path: Path, *, offer_id: str, candidate: dict[str, Any],
    stage_root: Path, gate: str, result: str, tested_head: str,
    evidence_ref: str, evidence_sha256: str,
    authenticated_owner_check: Callable[[], bool] | None = None,
    active_lease_check: Callable[[], bool] | None = None,
    independent_evidence_check: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Reference externally validated test/CI evidence; do not assert PASS for it.

    The protected resident controller supplies all three callbacks. Never
    expose this function as a generic write-enabled MCP or hosted ChatGPT tool.
    The evidence callback MUST independently re-read native test/CI records,
    not inspect a worker's prose or merely trust the requested result.
    """
    if (type(gate) is not str or gate not in _GATES
            or type(result) is not str or result not in _RESULTS
            or not isinstance(tested_head, str) or not _SHA40.fullmatch(tested_head)
            or not _safe_evidence_ref(evidence_ref)
            or not isinstance(evidence_sha256, str) or not _SHA256.fullmatch(evidence_sha256)):
        raise offload.OffloadRejected("VERIFICATION_EVIDENCE_SHAPE_INVALID")
    if (authenticated_owner_check is None or active_lease_check is None
            or independent_evidence_check is None):
        raise offload.OffloadRejected("INDEPENDENT_VERIFICATION_GATES_REQUIRED")

    offer = offload.read_offer(db_path, offer_id)
    offload.require_registered_candidate(db_path, offer, candidate, stage_root)
    offload.verify_candidate(offer, candidate)
    body = {
        "schema_version": 1, "offer_id": offer_id,
        "repository": offer["repository"], "slice_id": offer["slice_id"],
        "base_sha": offer["base_sha"], "patch_sha256": candidate["patch_sha256"],
        "manifest_sha256": candidate["manifest_sha256"], "gate": gate,
        "result": result, "tested_head": tested_head,
        "evidence_ref": evidence_ref, "evidence_sha256": evidence_sha256,
    }
    if (authenticated_owner_check() is not True or active_lease_check() is not True
            or independent_evidence_check(dict(body)) is not True):
        raise offload.OffloadRejected("INDEPENDENT_VERIFICATION_EVIDENCE_DENIED")
    # Re-hash after the external witness callback to catch byte changes while
    # a native verifier was inspecting its own test/CI records.
    offload.verify_candidate(offer, candidate)

    serialized = _canonical(body)
    receipt_id = hashlib.sha256(serialized.encode("ascii")).hexdigest()
    _schema(db_path)
    with closing(offload.coordination._connect(db_path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            if authenticated_owner_check() is not True or active_lease_check() is not True:
                raise offload.OffloadRejected("VERIFICATION_AUTHORITY_REVOKED")
            current = db.execute(
                "SELECT receipt_id,body FROM offload_verification_receipts "
                "WHERE offer_id=? AND gate=? AND evidence_ref=?",
                (offer_id, gate, evidence_ref),
            ).fetchone()
            if current is None:
                count = db.execute(
                    "SELECT count(*) FROM offload_verification_receipts WHERE offer_id=?",
                    (offer_id,),
                ).fetchone()[0]
                if count >= _LIMIT:
                    raise offload.OffloadRejected("OFFLOAD_VERIFICATION_RECEIPTS_LIMIT")
                db.execute(
                    "INSERT INTO offload_verification_receipts "
                    "(receipt_id,offer_id,gate,evidence_ref,body) VALUES (?,?,?,?,?)",
                    (receipt_id, offer_id, gate, evidence_ref, serialized),
                )
                replayed = False
            elif current["receipt_id"] == receipt_id and current["body"] == serialized:
                replayed = True
            else:
                raise offload.OffloadRejected("CONFLICTING_VERIFICATION_EVIDENCE_REPLAY")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return {
        "status": "EXTERNALLY_CHECKED_EVIDENCE_RECORDED",
        "receipt_id": receipt_id, "offer_id": offer_id,
        "gate": gate, "result": result, "replayed": replayed,
        "execution_authorized": False, "merge_authorized": False,
        "skill_promoted": False,
    }


def read_verification_references(db_path: Path, *, offer_id: str, limit: int = 15) -> dict[str, Any]:
    """Bounded advisory-only receipt view; no raw test logs or private code."""
    if type(limit) is not int or not 1 <= limit <= _LIMIT:
        raise offload.OffloadRejected("INVALID_VERIFICATION_PAGE_SIZE")
    offer = offload.read_offer(db_path, offer_id)
    with closing(offload.coordination._connect(db_path, read_only=True)) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='offload_verification_receipts'",
        ).fetchone()
        rows = [] if exists is None else db.execute(
            "SELECT receipt_id,body FROM offload_verification_receipts "
            "WHERE offer_id=? ORDER BY created_at DESC,receipt_id LIMIT ?",
            (offer_id, limit),
        ).fetchall()
    result = []
    for row in rows:
        try:
            item = json.loads(row["body"])
        except (ValueError, TypeError):
            continue
        if (not isinstance(item, dict) or item.get("schema_version") != 1
                or item.get("offer_id") != offer_id
                or item.get("repository") != offer["repository"]
                or item.get("base_sha") != offer["base_sha"]
                or type(item.get("gate")) is not str or item["gate"] not in _GATES
                or type(item.get("result")) is not str or item["result"] not in _RESULTS
                or not _safe_evidence_ref(item.get("evidence_ref"))
                or not isinstance(item.get("evidence_sha256"), str)
                or not _SHA256.fullmatch(item["evidence_sha256"])
                or not isinstance(item.get("tested_head"), str)
                or not _SHA40.fullmatch(item["tested_head"])
                or _canonical(item) != row["body"]
                or hashlib.sha256(_canonical(item).encode("ascii")).hexdigest() != row["receipt_id"]):
            continue
        result.append({
            "receipt_id": row["receipt_id"], "gate": item.get("gate"),
            "result": item.get("result"), "tested_head": item.get("tested_head"),
            "evidence_ref": item.get("evidence_ref"),
            "evidence_sha256": item.get("evidence_sha256"),
        })
    return {
        "status": "OK", "offer_id": offer_id, "items": result,
        "authority": "ADVISORY_ONLY", "execution_authorized": False,
        "merge_authorized": False, "learning_promotion_authorized": False,
    }
