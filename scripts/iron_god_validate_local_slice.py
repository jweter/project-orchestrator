"""Will the LOCAL builder accept this capability-graph slice?  (offline, deterministic)

When the capable builder's daily budget is spent the engine can still develop code, but only slices the free local model
is measured for: Level 1 (one new pure function with literal acceptance examples) and Level 2 (a directed edit with an
approved test). A slice that does not meet the local contract is not built locally, and the engine's first sign is a
silent hand-off to the capable builder or a rejected plan. This checker runs the same rules the engine applies, before
anything is committed, and says exactly what to fix. See docs/LOCAL_WORK_BACKLOG.md for the authoring contract.

Usage:
    python scripts/iron_god_validate_local_slice.py --graph engineering/capability-graph.json \
        --repository jweter/<name> --slice <slice-id> [--state-root <flow-state dir>]

Exit status 0 = no problems (the route line says whether it is executable now and routes LOCAL), 1 = problems found.
Without ``--state-root`` the measured-capability profile is not consulted and the routing check is skipped with a note.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT = 1200  # the Canon worker bounds the objective and each acceptance criterion


def _slice_of(graph: dict[str, Any], slice_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for capability in graph.get("capabilities", []):
        for candidate in capability.get("slices", []):
            if candidate.get("id") == slice_id:
                return capability, candidate
    return None


def check_slice(graph: dict[str, Any], slice_id: str, repository: str, *, profile: dict[str, Any] | None = None,
                expected_model: str | None = None, max_level: int = 2, skip_route: bool = False) -> dict[str, Any]:
    """Problems that stop the local builder taking the slice, plus non-blocking notes."""
    from project_orchestrator import iron_god_local_drafter as drafter
    from project_orchestrator import iron_god_roadmap_refill as refill
    from project_orchestrator import iron_god_task_decomposer as decomposer

    problems: list[str] = []
    notes: list[str] = []
    found = _slice_of(graph, slice_id)
    if found is None:
        return {"problems": [f"slice {slice_id!r} is not in the graph"], "notes": [], "executable_now": False}
    _capability, spec = found

    level = spec.get("capability_level")
    if level not in (1, 2):
        problems.append(f"capability_level must be 1 or 2 for the local builder (found {level!r}); anything higher goes to the capable builder")

    for label, text in [("objective", spec.get("objective")), *((f"acceptance[{i}]", a) for i, a in enumerate(spec.get("acceptance") or []))]:
        if not isinstance(text, str) or not text.strip():
            problems.append(f"{label} must be a non-empty string")
        elif len(text) > MAX_TEXT:
            problems.append(f"{label} is {len(text)} characters; the local worker accepts at most {MAX_TEXT}")

    allowed = list(spec.get("allowed_paths") or [])
    forbidden = list(spec.get("forbidden_paths") or [])
    if not allowed:
        problems.append("allowed_paths is empty: the local builder needs to know where the code may go")

    if level == 1:
        raw = spec.get("acceptance_examples")
        if not isinstance(raw, list) or not raw:
            problems.append("a Level 1 slice needs acceptance_examples authored by a stronger agent, not left for the 7B to invent "
                            "(its own examples are often contradictory)")
        else:
            usable = drafter.roadmap_examples({"acceptance_examples": raw})
            if len(usable) != len(raw):
                problems.append("every acceptance example needs a '{fn}(...)' call with literal arguments, an 'expected' value that "
                                "is a Python literal, and a non-empty 'why'")
            if len(usable) < drafter.MIN_ROADMAP_EXAMPLES:
                problems.append(f"at least {drafter.MIN_ROADMAP_EXAMPLES} usable acceptance examples are required (found {len(usable)})")
            seen: dict[str, Any] = {}
            for example in usable:
                if example["call"] in seen and seen[example["call"]] != example["expected"]:
                    problems.append(f"contradictory expected values for {example['call']}")
                seen[example["call"]] = example["expected"]
                if decomposer.PLACEHOLDER_PATH.search(example["call"]):
                    problems.append(f"{example['call']} uses a 'path/to/' placeholder: a pure function cannot be specified against a file that does not exist")
        request_like = {"allowed_paths": allowed, "forbidden_paths": forbidden}
        if decomposer.module_for(request_like, "validator_probe", set(), lambda _p: False) is None:
            problems.append("allowed_paths hold no place for a NEW Python module (needs a code pattern such as 'pkg/**', not only tests/docs/json)")
    elif level == 2:
        if drafter.directed_spec({"directed_edit": spec.get("directed_edit"), "capability_level": 2}) is None:
            problems.append("a Level 2 slice needs a complete directed_edit (module_path, test_path, test_source, targets) with a NEW approved test file "
                            "that fails at the base for the right reason")

    executable_now = False
    derived = refill.derive(repository, "0" * 40, graph, "engineering/capability-graph.json", items=[], state={})
    mine = [r for r in derived["requests"] if r.get("slice") == slice_id]
    if mine:
        executable_now = True
    else:
        reasons = [d.get("reason") for d in derived.get("direction_required", [])]
        notes.append("not executable right now (a dependency or an earlier slice of its capability comes first)"
                     + (f"; engine says {reasons}" if reasons else ""))

    if skip_route:
        notes.append("routing check skipped (no measured-capability profile supplied)")
    elif level in (1, 2):
        route = refill.route({"capability_level": level}, profile=profile, max_level=max_level,
                             expected_model=expected_model or refill.DEFAULT_MODEL)
        if route.get("builder") != refill.LOCAL:
            problems.append(f"the engine would route this slice to {route.get('builder')}: {route.get('reason')}")
        else:
            notes.append(f"routes LOCAL ({route.get('reason')})")
    return {"problems": problems, "notes": notes, "executable_now": executable_now}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that a capability-graph slice meets the local builder's contract.")
    parser.add_argument("--graph", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--slice", required=True, dest="slice_id")
    parser.add_argument("--state-root", type=Path, default=None, help="engine flow-state directory (reads the measured model profile)")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    from project_orchestrator import iron_god_model_routing as model_routing
    from project_orchestrator import iron_god_roadmap_refill as refill

    graph = json.loads(args.graph.read_text(encoding="utf-8-sig"))
    profile = None
    expected_model = None
    if args.state_root is not None:
        best = model_routing.best_profile(refill.load_profiles(args.state_root))
        profile = best or refill.load_profile(args.state_root)
        expected_model = best["model"] if best else refill.DEFAULT_MODEL
    result = check_slice(graph, args.slice_id, args.repository, profile=profile, expected_model=expected_model,
                         skip_route=args.state_root is None)
    for note in result["notes"]:
        print(f"note: {note}")
    for problem in result["problems"]:
        print(f"PROBLEM: {problem}")
    print(("PASS" if not result["problems"] else "FAIL") + f" {args.slice_id} (executable now: {result['executable_now']})")
    return 0 if not result["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
