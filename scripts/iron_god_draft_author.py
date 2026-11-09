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
import re
import subprocess
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
    given = [e for e in request.get("acceptance_examples", []) if isinstance(e, dict) and "call" in e and "expected" in e]
    if given:
        decomposition = request.get("decomposition") or {}
        self_authored = (isinstance(decomposition, dict)
                         and decomposition.get("oracle") == "SELF_AUTHORED")
        if self_authored:
            lines.append("PROVENANCE: these acceptance examples were proposed by a model, NOT independently "
                         "approved by the owner. They are provisional and cannot certify online evidence. "
                         "Never invent results or placeholder content to satisfy them:")
        else:
            lines.append("The owner's acceptance examples (your function name replaces {fn}; they are added to "
                         "your tests and your function MUST satisfy every one; never contradict them):")
        lines += [f"- {e['call']} -> {json.dumps(e['expected'])}" for e in given]
    if request.get("prior_failures"):
        lines.append("Earlier attempts failed; plan differently: " + json.dumps(request["prior_failures"])[:600])
    if request.get("planner_feedback"):
        lines.append("Previous draft was rejected for: " + str(request["planner_feedback"])[:600])
    return "\n".join(lines)


BROKEN_BEFORE_THE_TEST_RAN = re.compile(r"ImportError|ModuleNotFoundError|SyntaxError|NameError|ERROR collecting|"
                                        r"fixture '.*' not found|no tests ran", re.IGNORECASE)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=600, check=False)


def verification_worktree(clone: Path, base: str) -> Path:
    """One persistent detached worktree of the lane clone, positioned at ``base``, for proving an approved test fails.

    It must belong to the lane clone (same common Git directory): the worker finds the repository's installed
    Poetry/uv environment through that directory, so a standalone checkout would rebuild every dependency. The
    worktree is created once and only ever re-positioned afterwards - never deleted - and holds nothing but the
    approved test file, which is removed again before the next use.
    """
    path = clone.parent / "draft-check" / clone.name
    if (path / ".git").exists():
        _git(path, "reset", "--quiet", "--hard")
        _git(path, "clean", "-fdq")
        moved = _git(path, "checkout", "--quiet", "--detach", "--force", base)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        moved = _git(clone, "worktree", "add", "--quiet", "--detach", str(path), base)
    if moved.returncode != 0:
        raise RuntimeError("DIRECTED_BASE_CHECKOUT_FAILED: " + (moved.stderr or "")[-200:])
    return path


def directed_direction(request: dict[str, Any], spec: dict[str, Any]) -> str:
    """The behavioural requirement the worker's model is given: the owner's own words, never invented here."""
    stated = " ".join(str(spec.get("direction") or "").split())
    if stated:
        return stated
    criteria = "; ".join(str(item) for item in request.get("slice_acceptance", []))
    return " ".join(f"{request['objective']} Acceptance: {criteria}".split())[:1000]


def directed_validator(worker: Any, authority: Callable[[], bool], execute: Callable[..., dict[str, Any]] | None = None
                       ) -> Callable[..., dict[str, Any]]:
    """Validate the owner's declared directed edit and PROVE its approved test fails at the exact base.

    The edit is checked by the worker's own ``validate_direct``; then, in a private checkout of the exact base, the
    approved test is run in the repository's own environment. The draft is accepted only when pytest reports failing
    assertions (exit 1) - not when the test passes already (it would approve nothing) and not when it cannot run
    (an import or collection error is a broken test, not a failing requirement). The worker repeats the check later.
    """
    run_tests = execute or worker.repository_test

    def check() -> None:
        if not authority():
            raise RuntimeError("EXECUTION_AUTHORITY_REVOKED")

    def validate(raw: dict[str, Any], clone: Path, base: str, packet: dict[str, Any]) -> dict[str, Any]:
        directed = dict(raw)
        tree = verification_worktree(clone, base)
        try:
            validated = worker.validate_direct(directed, tree, base, packet)
            if not validated["test_on_main"]:
                target = tree / validated["test_path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(validated["test_source"], encoding="utf-8")
            before = run_tests(tree, validated["test_path"], check)
            tail = str(before.get("output_tail", ""))
            if before.get("ok") or before.get("returncode") == 0:
                raise RuntimeError("APPROVED_TEST_ALREADY_PASSES_AT_BASE")
            if before.get("returncode") != 1 or BROKEN_BEFORE_THE_TEST_RAN.search(tail):
                raise RuntimeError("APPROVED_TEST_DOES_NOT_RUN_AT_BASE: " + tail[-300:])
            validated["approved_base"] = base
            validated["verified_failing"] = {"returncode": before.get("returncode"), "tail": tail[-1200:]}
            return validated
        finally:
            _git(tree, "reset", "--quiet", "--hard")  # the approved test file is the only thing ever written here
            _git(tree, "clean", "-fdq")

    return validate


def planning_roots(allowed_paths: list[str]) -> list[str]:
    """Directories the planned module may live in: the prefix before a glob, or the directory of a single named file."""
    roots = set()
    for path in allowed_paths:
        if path.startswith("tests/"):
            continue
        root = path.split("*")[0].rstrip("/") + "/" if "*" in path else (path.rsplit("/", 1)[0] + "/" if "/" in path else "")
        if root:
            roots.add(root)
    return sorted(roots)


def build_packet(request: dict[str, Any], roots: list[str], base: str, forbidden: list[str], attempted: list[str]) -> dict[str, Any]:
    return {"repository": request["repository"], "base_sha": base, "paths": roots,
            "planning": {"forbidden_paths": forbidden}, "avoid_paths": request.get("reserved_conflicts", []),
            "attempted_functions": attempted, "working_memory": "",
            "declared_tests": [p for p in request.get("allowed_paths", []) if str(p).startswith("tests/")]}


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
    roots = planning_roots(request["allowed_paths"])
    planner, validator = worker.production_planner, worker.validate_plan
    if request.get("capability_level") == 2:  # a directed edit: the owner declared it, no model plans anything
        declared = dict(request["directed_edit"])
        declared["direction"] = directed_direction(request, declared)
        planner, validator = (lambda *_a: declared), directed_validator(worker, authority)
    run(request, args.clone, args.base, args.result, planner=planner, validator=validator,
        authority=authority, roots=roots, forbidden=[*ALWAYS_FORBIDDEN, *request["forbidden_paths"]],
        attempted=request.get("attempted_functions", []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
