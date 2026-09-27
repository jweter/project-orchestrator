from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from project_orchestrator.learning import validate_lesson

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE = ROOT / "engineering" / "learning-candidates.json"

ALLOWED_PROJECTS = {"portfolio", "knowledge-engine", "rocksmith", "everward", "voicestudio"}
ALLOWED_VERIFICATION_AUTHORITIES = {
    "DETERMINISTIC_CI",
    "REPOSITORY_NATIVE_TESTS",
    "SPECIALIST_REVIEW",
    "HUMAN_REVIEW",
    "PRODUCT_REALITY_REVIEW",
}
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(password|api[_-]?key|secret|token)\s*[:=]\s*[^\s]+"),
)
REQUIRED_FIELDS = {
    "project",
    "repository",
    "observation",
    "proposed_rule",
    "evidence_refs",
    "source_harness",
    "created_at",
    "status",
    "authority",
    "confidence",
    "product_reality_may_be_inferred",
}


class LearningCandidateError(ValueError):
    """Raised when candidate learning violates its non-authoritative contract."""


def _contains_secret(value: Any) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in SECRET_PATTERNS)
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_secret(key) or _contains_secret(item) for key, item in value.items())
    return False


def _candidate_identity(candidate: dict[str, Any]) -> str:
    stable = {key: candidate[key] for key in sorted(REQUIRED_FIELDS)}
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def validate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise LearningCandidateError("candidate must be an object")
    missing = sorted(REQUIRED_FIELDS - set(candidate))
    if missing:
        raise LearningCandidateError(f"candidate missing required fields: {missing}")

    if candidate["project"] not in ALLOWED_PROJECTS:
        raise LearningCandidateError("candidate project is not in the portfolio")
    for field in ("repository", "observation", "proposed_rule", "source_harness", "created_at"):
        if not isinstance(candidate[field], str) or not candidate[field].strip():
            raise LearningCandidateError(f"{field} must be a non-empty string")
    if candidate["status"] != "CANDIDATE":
        raise LearningCandidateError("candidate status must remain CANDIDATE")
    if candidate["authority"] != "NON_AUTHORITATIVE":
        raise LearningCandidateError("candidate authority must remain NON_AUTHORITATIVE")
    if candidate["product_reality_may_be_inferred"] is not False:
        raise LearningCandidateError("candidate learning may not infer Product Reality")
    refs = candidate["evidence_refs"]
    if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref.strip() for ref in refs):
        raise LearningCandidateError("evidence_refs must be a non-empty string list")
    confidence = candidate["confidence"]
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= float(confidence) <= 1
    ):
        raise LearningCandidateError("confidence must be a number between 0 and 1")
    if _contains_secret(candidate):
        raise LearningCandidateError("candidate appears to contain secret material")

    normalized = {key: candidate[key] for key in REQUIRED_FIELDS}
    normalized["confidence"] = float(confidence)
    normalized["evidence_refs"] = list(refs)
    normalized["candidate_id"] = _candidate_identity(normalized)
    supplied = candidate.get("candidate_id")
    if supplied is not None and supplied != normalized["candidate_id"]:
        raise LearningCandidateError("candidate_id does not match candidate content")
    return normalized


def validate_store(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise LearningCandidateError("candidate store schema_version must equal 1")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise LearningCandidateError("candidate store entries must be a list")
    normalized = []
    seen = set()
    for raw in entries:
        item = validate_candidate(raw)
        if item["candidate_id"] in seen:
            raise LearningCandidateError(f"duplicate candidate_id: {item['candidate_id']}")
        seen.add(item["candidate_id"])
        normalized.append(item)
    return {"schema_version": 1, "entries": normalized}


def load_store(path: str | Path = DEFAULT_STORE) -> dict[str, Any]:
    return validate_store(json.loads(Path(path).read_text(encoding="utf-8")))


def build_promotion_packet(candidate: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    """Build a VERIFIED lesson packet without mutating authoritative memory."""
    item = validate_candidate(candidate)
    if not isinstance(verification, dict):
        raise LearningCandidateError("verification must be an object")

    required = {
        "verification_authority",
        "independent_verifier",
        "source_type",
        "source_ref",
        "root_cause",
        "fix",
        "verification",
        "regression_protection",
        "residual_risk",
    }
    missing = sorted(required - set(verification))
    if missing:
        raise LearningCandidateError(f"verification missing required fields: {missing}")
    if verification["verification_authority"] not in ALLOWED_VERIFICATION_AUTHORITIES:
        raise LearningCandidateError("verification_authority is not allowed")
    verifier = verification["independent_verifier"]
    if not isinstance(verifier, str) or not verifier.strip():
        raise LearningCandidateError("independent_verifier must be non-empty")
    if verifier.strip().lower() == item["source_harness"].strip().lower():
        raise LearningCandidateError("candidate source may not act as its own independent verifier")
    for field in required - {"verification_authority", "independent_verifier"}:
        if not isinstance(verification[field], str) or not verification[field].strip():
            raise LearningCandidateError(f"verification.{field} must be non-empty")
    if _contains_secret(verification):
        raise LearningCandidateError("verification appears to contain secret material")

    lesson = validate_lesson(
        {
            "project": item["project"],
            "repository": item["repository"],
            "source_type": verification["source_type"],
            "source_ref": verification["source_ref"],
            "symptom": item["observation"],
            "root_cause": verification["root_cause"],
            "fix": verification["fix"],
            "verification": verification["verification"],
            "regression_protection": verification["regression_protection"],
            "residual_risk": verification["residual_risk"],
            "evidence_state": "VERIFIED",
        }
    )
    return {
        "schema_version": 1,
        "candidate_id": item["candidate_id"],
        "promotion_state": "READY_FOR_EXPLICIT_PROMOTION",
        "auto_promoted": False,
        "candidate_confidence_is_authority": False,
        "verification_authority": verification["verification_authority"],
        "independent_verifier": verifier,
        "lesson": lesson,
    }


def _find_candidate(store: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    normalized = validate_store(store)
    for item in normalized["entries"]:
        if item["candidate_id"] == candidate_id:
            return item
    raise LearningCandidateError(f"candidate not found: {candidate_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Non-authoritative learning candidate bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    validate_cmd = sub.add_parser("validate")
    validate_cmd.add_argument("--store", default=str(DEFAULT_STORE))

    report_cmd = sub.add_parser("report")
    report_cmd.add_argument("--store", default=str(DEFAULT_STORE))

    promote_cmd = sub.add_parser("promotion-packet")
    promote_cmd.add_argument("--store", default=str(DEFAULT_STORE))
    promote_cmd.add_argument("--candidate-id", required=True)
    promote_cmd.add_argument("--verification", required=True)

    args = parser.parse_args(argv)
    store = load_store(args.store)

    if args.command == "validate":
        print(json.dumps({"status": "GREEN", "candidate_count": len(store["entries"])}, indent=2))
        return 0
    if args.command == "report":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "candidate_count": len(store["entries"]),
                    "authoritative_candidate_count": 0,
                    "auto_promotion_allowed": False,
                    "product_reality_inferred": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    verification = json.loads(Path(args.verification).read_text(encoding="utf-8"))
    packet = build_promotion_packet(_find_candidate(store, args.candidate_id), verification)
    print(json.dumps(packet, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

\n