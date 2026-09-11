from __future__ import annotations

from typing import Any

from .throughput_guard import build_fast_gate_plan


def build_repository_fast_gate_plan(
    *,
    repository: str,
    control_plane: dict[str, Any],
    changed_paths: list[str],
) -> dict[str, Any]:
    """Build FAST_GATE from explicit policy, falling back to preflight ``fast`` checks.

    Several product repositories already classify cheap checks with ``fast: true``.
    That declaration is repository-owned command authority and can be reused without
    forcing an otherwise-no-op control-plane migration. Full preflight remains required.
    """
    explicit = build_fast_gate_plan(
        repository=repository,
        control_plane=control_plane,
        changed_paths=changed_paths,
    )
    if explicit["status"] != "NOT_AUTHORIZED":
        return explicit

    preflight = control_plane.get("preflight")
    if not isinstance(preflight, dict):
        return explicit
    checks = preflight.get("checks")
    if not isinstance(checks, list):
        return explicit

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for check in checks:
        if not isinstance(check, dict) or check.get("fast") is not True:
            continue
        check_id = check.get("id")
        argv = check.get("argv")
        if not isinstance(check_id, str) or not check_id.strip():
            raise ValueError("preflight fast check id must be a non-empty string")
        if not isinstance(argv, list) or not argv or not all(isinstance(part, str) and part for part in argv):
            raise ValueError(f"preflight fast check {check_id} argv must be a non-empty string list")
        key = tuple(argv)
        if key in seen:
            continue
        seen.add(key)
        selected.append({"id": check_id, "argv": list(argv)})

    if not selected:
        return explicit
    return {
        "schema_version": 1,
        "repository": repository,
        "status": "READY",
        "authority_source": "preflight.checks[].fast=true",
        "changed_paths": list(changed_paths),
        "commands": selected,
        "canonical_preflight_still_required": True,
    }
