from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / "engineering" / "harness-audit.json"
DEFAULT_REPOSITORY = "jweter/project-orchestrator"

VALID_KINDS = {
    "INSTRUCTION",
    "AGENT",
    "SKILL",
    "MCP_DESCRIPTOR",
    "TOOL_DESCRIPTOR",
    "HOOK",
}
VALID_ACTIVATIONS = {"ALWAYS_ON", "TASK_TRIGGERED", "TOOL_DESCRIPTOR", "EVENT"}
CONTEXT_ACTIVATIONS = {"ALWAYS_ON", "TASK_TRIGGERED", "TOOL_DESCRIPTOR"}


def load_policy(path: str | Path = DEFAULT_POLICY) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("harness audit policy must be a JSON object")
    return value


def audit_harness(
    policy: dict[str, Any],
    repo_roots: dict[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Audit repository-resident harness surfaces without mutating them.

    This is an observability layer, not a new source of engineering authority.
    Repositories that are not mounted are explicitly UNVERIFIED. Context
    measurements are advisory evidence and cannot authorize merge or Product
    Reality claims.
    """

    roots = {name: Path(path) for name, path in (repo_roots or {}).items()}
    findings: list[dict[str, Any]] = []

    def finding(
        code: str,
        severity: str,
        message: str,
        *,
        repository: str | None = None,
        path: str | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "code": code,
            "severity": severity,
            "message": message,
        }
        if repository is not None:
            item["repository"] = repository
        if path is not None:
            item["path"] = path
        findings.append(item)

    _validate_policy(policy, finding)

    budget = _mapping(policy.get("context_budget"))
    chars_per_token = _positive_int(budget.get("approx_chars_per_token")) or 4
    repositories = policy.get("repositories") if isinstance(policy.get("repositories"), list) else []
    patterns = policy.get("surface_patterns") if isinstance(policy.get("surface_patterns"), list) else []

    reports: list[dict[str, Any]] = []
    all_surfaces: list[dict[str, Any]] = []

    for repo_cfg_raw in repositories:
        repo_cfg = _mapping(repo_cfg_raw)
        name = repo_cfg.get("name") if _nonempty(repo_cfg.get("name")) else "UNKNOWN"
        root = roots.get(name)

        overlap = set(_string_list(repo_cfg.get("authoritative_paths"))) & set(
            _string_list(repo_cfg.get("advisory_paths"))
        )
        if overlap:
            finding(
                "AUTHORITY_OVERLAP",
                "ERROR",
                f"paths cannot be both authoritative and advisory: {sorted(overlap)}",
                repository=name,
            )

        if root is None:
            finding(
                "REPOSITORY_UNVERIFIED",
                "INFO",
                "repository root was not supplied; remote harness state remains UNVERIFIED",
                repository=name,
            )
            reports.append(
                {
                    "repository": name,
                    "state": "UNVERIFIED",
                    "root": None,
                    "surface_count": 0,
                    "context": _empty_context(),
                    "surfaces": [],
                }
            )
            continue

        if not root.exists() or not root.is_dir():
            finding(
                "REPOSITORY_ROOT_UNAVAILABLE",
                "ERROR",
                f"supplied repository root does not exist or is not a directory: {root}",
                repository=name,
            )
            reports.append(
                {
                    "repository": name,
                    "state": "UNAVAILABLE",
                    "root": str(root),
                    "surface_count": 0,
                    "context": _empty_context(),
                    "surfaces": [],
                }
            )
            continue

        for required in _string_list(repo_cfg.get("required_paths")):
            if not (root / required).exists():
                finding(
                    "MISSING_REQUIRED_SURFACE",
                    "ERROR",
                    "required harness/authority surface is missing",
                    repository=name,
                    path=required,
                )

        surfaces = discover_surfaces(root, patterns, chars_per_token=chars_per_token)
        all_surfaces.extend({**surface, "repository": name} for surface in surfaces)

        for surface in surfaces:
            if surface["read_error"] is not None:
                finding(
                    "SURFACE_READ_FAILURE",
                    "ERROR",
                    surface["read_error"],
                    repository=name,
                    path=surface["path"],
                )
            if surface["kind"] == "MCP_DESCRIPTOR" and surface["text"] is not None:
                for key_path in _secret_like_literals(
                    surface["text"],
                    key_markers=_string_list(policy.get("secret_key_markers")),
                    safe_value_markers=_string_list(policy.get("safe_secret_value_markers")),
                ):
                    finding(
                        "SECRET_LIKE_LITERAL",
                        _secret_severity(policy),
                        f"MCP descriptor contains a literal value under secret-like key {key_path}",
                        repository=name,
                        path=surface["path"],
                    )

        _add_duplicate_findings(name, surfaces, finding)
        context = _context_summary(surfaces)
        _add_budget_findings(name, context, surfaces, budget, finding)

        reports.append(
            {
                "repository": name,
                "state": "AUDITED",
                "root": str(root),
                "surface_count": len(surfaces),
                "context": context,
                "surfaces": [_public_surface(surface) for surface in surfaces],
            }
        )

    severities = {item["severity"] for item in findings}
    status = "RED" if "ERROR" in severities else "YELLOW" if findings else "GREEN"

    audited = sum(1 for report in reports if report["state"] == "AUDITED")
    unverified = sum(1 for report in reports if report["state"] == "UNVERIFIED")
    unavailable = sum(1 for report in reports if report["state"] == "UNAVAILABLE")
    total_context_bytes = sum(
        int(report["context"]["total_context_bytes"])
        for report in reports
        if report["state"] == "AUDITED"
    )
    total_estimated_tokens = sum(
        int(report["context"]["estimated_tokens"])
        for report in reports
        if report["state"] == "AUDITED"
    )

    return {
        "schema_version": 1,
        "mode": policy.get("mode", "UNKNOWN"),
        "status": status,
        "read_only": True,
        "product_reality_inferred": False,
        "context_metrics_are_merge_authority": False,
        "summary": {
            "repositories_declared": len(reports),
            "repositories_audited": audited,
            "repositories_unverified": unverified,
            "repositories_unavailable": unavailable,
            "surface_count": len(all_surfaces),
            "total_context_bytes": total_context_bytes,
            "estimated_tokens": total_estimated_tokens,
            "finding_count": len(findings),
            "error_count": sum(1 for item in findings if item["severity"] == "ERROR"),
            "warning_count": sum(1 for item in findings if item["severity"] == "WARNING"),
            "info_count": sum(1 for item in findings if item["severity"] == "INFO"),
        },
        "repositories": reports,
        "findings": findings,
    }


def discover_surfaces(
    root: Path,
    patterns: Iterable[Any],
    *,
    chars_per_token: int = 4,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    surfaces: list[dict[str, Any]] = []

    for raw in patterns:
        rule = _mapping(raw)
        glob = rule.get("glob")
        kind = rule.get("kind")
        activation = rule.get("activation")
        counts = rule.get("counts_toward_context") is True
        if not _nonempty(glob) or kind not in VALID_KINDS or activation not in VALID_ACTIVATIONS:
            continue

        for candidate in sorted(root.glob(glob)):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root).as_posix()
            if relative in seen:
                continue
            seen.add(relative)

            try:
                raw_bytes = candidate.read_bytes()
                text = raw_bytes.decode("utf-8", errors="replace")
                read_error = None
                sha256 = hashlib.sha256(raw_bytes).hexdigest()
                byte_count = len(raw_bytes)
                estimated_tokens = math.ceil(len(text) / chars_per_token) if text else 0
            except OSError as exc:
                raw_bytes = b""
                text = None
                read_error = str(exc)
                sha256 = None
                byte_count = 0
                estimated_tokens = 0

            surfaces.append(
                {
                    "path": relative,
                    "kind": kind,
                    "activation": activation,
                    "counts_toward_context": counts,
                    "bytes": byte_count,
                    "estimated_tokens": estimated_tokens,
                    "sha256": sha256,
                    "text": text,
                    "read_error": read_error,
                }
            )

    return surfaces


def _validate_policy(policy: dict[str, Any], finding: Any) -> None:
    if policy.get("schema_version") != 1:
        finding("INVALID_SCHEMA", "ERROR", "schema_version must equal 1")
    if policy.get("mode") != "READ_ONLY":
        finding("MUTATING_MODE_FORBIDDEN", "ERROR", "first-version harness audit must remain READ_ONLY")

    safety = _mapping(policy.get("safety"))
    required_safety = {
        "auto_delete": False,
        "auto_rewrite": False,
        "product_reality_may_be_inferred": False,
        "context_metrics_are_merge_authority": False,
        "repository_policy_remains_authoritative": True,
    }
    for key, expected in required_safety.items():
        if safety.get(key) is not expected:
            finding(
                "UNSAFE_POLICY",
                "ERROR",
                f"safety.{key} must equal {expected!r}",
            )

    budget = _mapping(policy.get("context_budget"))
    for key in (
        "always_on_max_bytes",
        "task_triggered_max_bytes",
        "single_surface_max_bytes",
        "approx_chars_per_token",
    ):
        if _positive_int(budget.get(key)) is None:
            finding("INVALID_SCHEMA", "ERROR", f"context_budget.{key} must be a positive integer")

    patterns = policy.get("surface_patterns")
    if not isinstance(patterns, list) or not patterns:
        finding("INVALID_SCHEMA", "ERROR", "surface_patterns must be a non-empty list")
    else:
        for index, raw in enumerate(patterns):
            rule = _mapping(raw)
            if not _nonempty(rule.get("glob")):
                finding("INVALID_SCHEMA", "ERROR", f"surface_patterns[{index}].glob must be non-empty")
            if rule.get("kind") not in VALID_KINDS:
                finding("INVALID_SCHEMA", "ERROR", f"surface_patterns[{index}].kind is invalid")
            if rule.get("activation") not in VALID_ACTIVATIONS:
                finding("INVALID_SCHEMA", "ERROR", f"surface_patterns[{index}].activation is invalid")
            if not isinstance(rule.get("counts_toward_context"), bool):
                finding(
                    "INVALID_SCHEMA",
                    "ERROR",
                    f"surface_patterns[{index}].counts_toward_context must be boolean",
                )

    repositories = policy.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        finding("INVALID_SCHEMA", "ERROR", "repositories must be a non-empty list")
        return

    names: list[str] = []
    for index, raw in enumerate(repositories):
        repo = _mapping(raw)
        name = repo.get("name")
        if not _nonempty(name):
            finding("INVALID_SCHEMA", "ERROR", f"repositories[{index}].name must be non-empty")
            continue
        names.append(name)
        for key in ("required_paths", "authoritative_paths", "advisory_paths"):
            if not _valid_string_list(repo.get(key)):
                finding("INVALID_SCHEMA", "ERROR", f"repositories[{index}].{key} must be a string list")

    if len(names) != len(set(names)):
        finding("DUPLICATE_REPOSITORY", "ERROR", "repository names in the harness inventory must be unique")


def _context_summary(surfaces: list[dict[str, Any]]) -> dict[str, int]:
    context_surfaces = [surface for surface in surfaces if surface["counts_toward_context"]]
    always_on = [surface for surface in context_surfaces if surface["activation"] == "ALWAYS_ON"]
    task_triggered = [surface for surface in context_surfaces if surface["activation"] != "ALWAYS_ON"]
    return {
        "always_on_bytes": sum(int(surface["bytes"]) for surface in always_on),
        "task_triggered_bytes": sum(int(surface["bytes"]) for surface in task_triggered),
        "total_context_bytes": sum(int(surface["bytes"]) for surface in context_surfaces),
        "estimated_tokens": sum(int(surface["estimated_tokens"]) for surface in context_surfaces),
        "always_on_surface_count": len(always_on),
        "task_triggered_surface_count": len(task_triggered),
    }


def _empty_context() -> dict[str, int]:
    return {
        "always_on_bytes": 0,
        "task_triggered_bytes": 0,
        "total_context_bytes": 0,
        "estimated_tokens": 0,
        "always_on_surface_count": 0,
        "task_triggered_surface_count": 0,
    }


def _add_budget_findings(
    repository: str,
    context: dict[str, int],
    surfaces: list[dict[str, Any]],
    budget: dict[str, Any],
    finding: Any,
) -> None:
    severity = budget.get("budget_exceeded_severity")
    if severity not in {"WARNING", "ERROR"}:
        severity = "WARNING"

    always_limit = _positive_int(budget.get("always_on_max_bytes"))
    task_limit = _positive_int(budget.get("task_triggered_max_bytes"))
    single_limit = _positive_int(budget.get("single_surface_max_bytes"))

    if always_limit is not None and context["always_on_bytes"] > always_limit:
        finding(
            "ALWAYS_ON_CONTEXT_BUDGET_EXCEEDED",
            severity,
            f"always-on context is {context['always_on_bytes']} bytes; budget is {always_limit}",
            repository=repository,
        )
    if task_limit is not None and context["task_triggered_bytes"] > task_limit:
        finding(
            "TASK_CONTEXT_BUDGET_EXCEEDED",
            severity,
            f"task-triggered/tool context is {context['task_triggered_bytes']} bytes; budget is {task_limit}",
            repository=repository,
        )
    if single_limit is not None:
        for surface in surfaces:
            if surface["counts_toward_context"] and int(surface["bytes"]) > single_limit:
                finding(
                    "CONTEXT_SURFACE_TOO_LARGE",
                    severity,
                    f"surface is {surface['bytes']} bytes; single-surface budget is {single_limit}",
                    repository=repository,
                    path=surface["path"],
                )


def _add_duplicate_findings(repository: str, surfaces: list[dict[str, Any]], finding: Any) -> None:
    by_hash: dict[str, list[dict[str, Any]]] = {}
    for surface in surfaces:
        if not surface["counts_toward_context"] or not surface["sha256"] or not surface["bytes"]:
            continue
        by_hash.setdefault(str(surface["sha256"]), []).append(surface)

    for group in by_hash.values():
        if len(group) < 2:
            continue
        paths = sorted(str(surface["path"]) for surface in group)
        always_on = any(surface["activation"] == "ALWAYS_ON" for surface in group)
        finding(
            "DUPLICATE_CONTEXT_SURFACE",
            "WARNING",
            f"exact duplicate context detected across {paths}; always_on={always_on}",
            repository=repository,
        )


def _secret_like_literals(
    text: str,
    *,
    key_markers: list[str],
    safe_value_markers: list[str],
) -> list[str]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return []

    markers = [marker.lower() for marker in key_markers if marker]
    safe_markers = [marker.lower() for marker in safe_value_markers if marker]
    findings: list[str] = []

    def walk(node: Any, path: list[str]) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                key_text = str(key)
                next_path = [*path, key_text]
                if isinstance(child, str) and any(marker in key_text.lower() for marker in markers):
                    candidate = child.strip()
                    lowered = candidate.lower()
                    if candidate and not any(marker in lowered for marker in safe_markers):
                        findings.append(".".join(next_path))
                walk(child, next_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, [*path, str(index)])

    walk(value, [])
    return findings


def _secret_severity(policy: dict[str, Any]) -> str:
    safety = _mapping(policy.get("safety"))
    value = safety.get("secret_like_literal_severity")
    return value if value in {"WARNING", "ERROR"} else "ERROR"


def _public_surface(surface: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in surface.items()
        if key not in {"text"}
    }


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _valid_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(_nonempty(item) for item in value)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _parse_repo_root(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--repo must use REPOSITORY=PATH")
    name, raw_path = value.split("=", 1)
    if not name.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("--repo must use non-empty REPOSITORY=PATH")
    return name.strip(), Path(raw_path).expanduser()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-orchestrator harness-audit")
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY,
        help="harness audit policy JSON",
    )
    parser.add_argument(
        "--repo",
        action="append",
        type=_parse_repo_root,
        default=[],
        metavar="REPOSITORY=PATH",
        help="repository root to audit; repeat for a full local portfolio workspace",
    )
    args = parser.parse_args(argv)

    roots = dict(args.repo)
    if not roots:
        roots[DEFAULT_REPOSITORY] = ROOT

    result = audit_harness(load_policy(args.policy), roots)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["status"] == "RED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
