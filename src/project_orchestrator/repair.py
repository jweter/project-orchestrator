from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Any


SAFE_CLASSES = {"FORMAT", "IMPORT_SORT", "LINT_SAFE_FIX"}
NON_AUTOFIX_CLASSES = {
    "TEST_FAILURE",
    "SECURITY",
    "DEPENDENCY",
    "TYPE_CHECK",
    "GOVERNANCE",
    "PROVENANCE",
    "PRODUCT_REALITY",
    "ENVIRONMENT",
    "UNKNOWN",
}
MAX_REPAIR_ITERATIONS = 3

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("FORMAT", re.compile(r"(would be reformatted|unformatted|ruff format|formatting)", re.I)),
    ("IMPORT_SORT", re.compile(r"(I001|import block is un-sorted|unsorted import)", re.I)),
    ("LINT_SAFE_FIX", re.compile(r"(\[\*\].*fixable|ruff check.*--fix|fixable with the .--fix. option)", re.I)),
    ("SECURITY", re.compile(r"(bandit|trufflehog|secret leak|security scan|vulnerabilit)", re.I)),
    ("DEPENDENCY", re.compile(r"(dependency|pip-audit|lock file|resolution impossible|version conflict)", re.I)),
    ("TYPE_CHECK", re.compile(r"(mypy|type check|typing error|incompatible type)", re.I)),
    ("GOVERNANCE", re.compile(r"(control plane|governance|policy validation|roadmap contract)", re.I)),
    ("PROVENANCE", re.compile(r"(provenance|license|copyright|private source|restricted material)", re.I)),
    ("PRODUCT_REALITY", re.compile(r"(product reality|playtest|packaged windows|unreal|real-game)", re.I)),
    ("ENVIRONMENT", re.compile(r"(command not found|no such file or directory|could not reach|runner|environment limitation)", re.I)),
    ("TEST_FAILURE", re.compile(r"(pytest|ctest|assertionerror|syntaxerror|traceback|\bFAILURES\b|\bFAILED\b)", re.I)),
]


@dataclass(frozen=True)
class FailureFingerprint:
    failure_class: str
    confidence: str
    evidence: tuple[str, ...]


def fingerprint_failure(log_text: str | None) -> FailureFingerprint:
    """Classify available log text; missing evidence must fail closed."""
    lines = [line.strip() for line in log_text.splitlines() if line.strip()] if isinstance(log_text, str) else []
    for failure_class, pattern in _PATTERNS:
        matches = tuple(line[:500] for line in lines if pattern.search(line))
        if matches:
            return FailureFingerprint(failure_class, "HIGH", matches[:5])
    return FailureFingerprint("UNKNOWN", "LOW", tuple(lines[-3:]))


def _argv(value: Any) -> list[str] | None:
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return list(value)
    return None


def approved_repair_commands(control_plane: dict[str, Any], failure_class: str) -> list[list[str]]:
    """Return only repository-explicit repair commands. Missing policy fails closed."""
    commands: list[list[str]] = []
    repair = control_plane.get("deterministic_repair")
    if isinstance(repair, dict):
        classes = repair.get("approved_classes")
        mapping = repair.get("commands")
        if (
            isinstance(classes, list)
            and failure_class in classes
            and isinstance(mapping, dict)
        ):
            raw = mapping.get(failure_class, [])
            if isinstance(raw, list):
                for item in raw:
                    argv = _argv(item)
                    if argv is not None:
                        commands.append(argv)

    # Backward-compatible repository authority already present in multiple product manifests.
    if failure_class == "FORMAT":
        preflight = control_plane.get("preflight")
        if isinstance(preflight, dict):
            argv = _argv(preflight.get("format_apply"))
            if argv is not None:
                commands.append(argv)

    # Deduplicate while preserving declared order.
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for command in commands:
        key = tuple(command)
        if key not in seen:
            seen.add(key)
            result.append(command)
    return result


def build_repair_plan(
    *,
    repository: str,
    log_text: str | None,
    control_plane: dict[str, Any],
    iteration: int = 1,
) -> dict[str, Any]:
    if iteration < 1:
        raise ValueError("iteration must be >= 1")
    fingerprint = fingerprint_failure(log_text)
    commands = approved_repair_commands(control_plane, fingerprint.failure_class)
    exhausted = iteration > MAX_REPAIR_ITERATIONS
    executable = (
        fingerprint.failure_class in SAFE_CLASSES
        and bool(commands)
        and not exhausted
    )
    reason: str
    if exhausted:
        reason = "repair iteration budget exhausted; park for retry or human/engineering diagnosis"
    elif fingerprint.failure_class not in SAFE_CLASSES:
        reason = "failure class is not eligible for deterministic auto-remediation"
    elif not commands:
        reason = "repository has not explicitly approved a command for this failure class"
    else:
        reason = "repository-approved deterministic repair is available"

    return {
        "schema_version": 1,
        "repository": repository,
        "failure_class": fingerprint.failure_class,
        "confidence": fingerprint.confidence,
        "evidence": list(fingerprint.evidence),
        "iteration": iteration,
        "max_iterations": MAX_REPAIR_ITERATIONS,
        "eligible_for_auto_repair": executable,
        "commands": commands if executable else [],
        "reason": reason,
        "requires_full_preflight_after_repair": executable,
        "safety_boundaries": [
            "execute only repository-explicit commands",
            "never auto-repair tests, security, governance, provenance, Product Reality, environment, or unknown failures",
            "never weaken or skip preflight/CI/review gates",
            "stop after bounded deterministic iterations",
        ],
    }


def execute_repair_plan(plan: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    """Execute a previously-built safe plan locally. Does not commit, push, open PRs, or merge."""
    if not plan.get("eligible_for_auto_repair"):
        return {**plan, "execution_status": "NOT_EXECUTED", "executions": []}

    executions: list[dict[str, Any]] = []
    for argv in plan.get("commands", []):
        completed = subprocess.run(
            argv,
            cwd=repo_root,
            text=True,
            capture_output=True,
        )
        executions.append(
            {
                "argv": argv,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
        )
        if completed.returncode != 0:
            return {**plan, "execution_status": "REPAIR_COMMAND_FAILED", "executions": executions}
    return {**plan, "execution_status": "REPAIR_COMMANDS_APPLIED", "executions": executions}


def load_control_plane(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("control-plane must be a JSON object")
    return data
