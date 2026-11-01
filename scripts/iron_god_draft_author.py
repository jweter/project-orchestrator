"""Plan one roadmap-derived Level 1 slice with the local model, in its own process (issue #594).

The roadmap refill produces an authoring *request* for each slice the local builder may take. This runner reuses the
product worker's own planning step (``production_planner``: the same prompt, the same Ollama call, the same
repository brief) with the request appended as the planner's feedback, then gates the answer with the worker's own
``validate_plan`` (one NEW pure function in a new module inside an existing package, literal examples that match the
signature). The result file carries the raw plan and the validated plan; the drafter module turns that into a backlog
draft and the strict admission in ``iron_god_roadmap_refill`` decides. A wrong or contradictory plan is rejected
mechanically, so a weak planner costs minutes, never a bad merge.

It runs in its own process like a product worker, bound to the release identity and the owner's execution authority.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]


def guide_text(request: dict[str, Any]) -> str:
    """The roadmap constraint appended to the worker's planning prompt."""
    lines = [
        "ROADMAP SLICE (mandatory): the function you plan must implement exactly this owner-approved slice.",
        f"Slice {request['id']} ({request['roadmap_ref']}): {request['objective']}",
        "Acceptance criteria:", *[f"- {item}" for item in request.get("slice_acceptance", [])],
        "The new module path MUST match one of these allowed paths: " + ", ".join(request["allowed_paths"]),
        "Never plan anything under: " + (", ".join(request["forbidden_paths"]) or "(none)"),
        "Examples must be literal calls with exact expected values that follow from the stated rule; cover every "
        "acceptance criterion, including invalid inputs (return the documented empty/neutral value, never raise "
        "unless the criteria say so).",
    ]
    if request.get("prior_failures"):
        lines.append("Earlier attempts failed; plan differently: " + json.dumps(request["prior_failures"])[:600])
    if request.get("planner_feedback"):
        lines.append("Previous draft was rejected for: " + str(request["planner_feedback"])[:600])
    return "\n".join(lines)


def build_packet(request: dict[str, Any], roots: list[str], base: str, forbidden: list[str], attempted: list[str]) -> dict[str, Any]:
    return {"repository": request["repository"], "base_sha": base, "paths": roots,
            "planning": {"forbidden_paths": forbidden}, "avoid_paths": request.get("reserved_conflicts", []),
            "attempted_functions": attempted, "working_memory": ""}


def run(request: dict[str, Any], clone: Path, base: str, result_path: Path, *, planner: Callable[..., Any],
        validator: Callable[..., dict[str, Any]], authority: Callable[[], bool], roots: list[str],
        forbidden: list[str], attempted: list[str]) -> dict[str, Any]:
    """Plan, validate, and write one result; every failure is a recorded failed result, never a hang or a pass."""

    def check() -> None:
        if not authority():
            raise RuntimeError("EXECUTION_AUTHORITY_REVOKED")

    result: dict[str, Any] = {"ok": False, "id": request["id"], "base": base}
    try:
        check()
        packet = build_packet(request, roots, base, forbidden, attempted)
        raw = planner(clone, packet, check, guide_text(request))
        result["plan"] = raw
        result["validated"] = validator(raw, clone, base, packet)
        result["ok"] = True
    except Exception as exc:  # PlanRejected, a model/transport error or revoked authority: all recorded, none a pass
        result["reason"] = f"{type(exc).__name__}: {str(exc)[:500]}"
    result["finished_at"] = datetime.now(UTC).isoformat()
    result_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = result_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2), encoding="utf-8")
    os.replace(tmp, result_path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan one roadmap slice with the local model.")
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--clone", required=True, type=Path)
    parser.add_argument("--base", required=True)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    from project_orchestrator.execution_authority import evaluate_execution_authority
    from project_orchestrator.iron_god_product_lane import ALWAYS_FORBIDDEN

    spec = importlib.util.spec_from_file_location("iron_god_product_worker", ROOT / "scripts" / "iron_god_product_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    release = os.environ.get("IRON_GOD_ACCEPTED_SHA")

    def authority() -> bool:
        try:
            return evaluate_execution_authority(release_sha=release).permitted is True
        except Exception:
            return False

    request = json.loads(args.request.read_text(encoding="utf-8"))
    roots = sorted({path.split("*")[0].rstrip("/") + "/" for path in request["allowed_paths"] if not path.startswith("tests/")})
    run(request, args.clone, args.base, args.result, planner=worker.production_planner, validator=worker.validate_plan,
        authority=authority, roots=roots, forbidden=[*ALWAYS_FORBIDDEN, *request["forbidden_paths"]],
        attempted=request.get("attempted_functions", []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
