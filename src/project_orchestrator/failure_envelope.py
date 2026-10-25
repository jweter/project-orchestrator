from __future__ import annotations

import hashlib
import re
from typing import Any

from .repair import SAFE_CLASSES, fingerprint_failure

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:ghp|github_pat|sk-proj|sk)-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"(?i)\b(?:token|password|secret|api[_-]?key)\s*[=:]\s*[^\s]+"),
)
_USER_PATHS = (
    re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+"),
    re.compile(r"/home/[^/\s]+"),
    re.compile(r"/Users/[^/\s]+"),
)


def sanitize_diagnostic(value: str, limit: int = 1200) -> str:
    text = " ".join(str(value).replace("\r", "\n").split())
    for pattern in _USER_PATHS:
        text = pattern.sub("<USER_HOME>", text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("<REDACTED>", text)
    return text[:limit] or "UNKNOWN: no meaningful failure line found"


def first_meaningful_diagnostic(log_text: str | None) -> str:
    fingerprint = fingerprint_failure(log_text)
    if fingerprint.evidence:
        return sanitize_diagnostic(fingerprint.evidence[0])
    lines = (
        [line for line in log_text.splitlines() if line.strip()]
        if isinstance(log_text, str)
        else []
    )
    return sanitize_diagnostic(lines[-1] if lines else "")


def retryability_for(failure_class: str) -> str:
    if failure_class in SAFE_CLASSES or failure_class in {
        "ENVIRONMENT",
        "WORKER_TIMEOUT",
        "RUNNER_CAPACITY_DEFERRED",
    }:
        return "RETRYABLE"
    if failure_class in {
        "SECURITY",
        "GOVERNANCE",
        "PROVENANCE",
        "PRODUCT_REALITY",
    }:
        return "BLOCKED_BY_AUTHORITY_OR_EXTERNAL_EVIDENCE"
    if failure_class == "UNKNOWN":
        return "UNKNOWN"
    return "ENGINEERING_REPAIR_REQUIRED"


def build_failure_envelope(
    *,
    repository: str,
    exact_head_sha: str,
    stage: str,
    log_text: str | None,
    failure_class: str | None = None,
    environment_identity: str | None = None,
) -> dict[str, Any]:
    fingerprint = fingerprint_failure(log_text)
    classification = failure_class or fingerprint.failure_class
    diagnostic = first_meaningful_diagnostic(log_text)
    stable = re.sub(r"\d+", "<n>", diagnostic.lower())
    digest = hashlib.sha256(
        f"{repository}|{stage}|{classification}|{stable}".encode("utf-8")
    ).hexdigest()[:20]
    return {
        "schema_version": 1,
        "stage": stage,
        "failure_class": classification,
        "failure_fingerprint": digest,
        "first_meaningful_diagnostic": diagnostic,
        "exact_head_sha": str(exact_head_sha),
        "environment_identity": environment_identity,
        "retryability": retryability_for(classification),
    }
