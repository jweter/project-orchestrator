"""Break an authorized roadmap slice the local builder cannot take into subtasks it can (Level 1), without a stronger model.

The product lane routes every roadmap slice by its declared capability level. The local builder is measured only for
Level 1 (one new pure function with literal acceptance examples) and Level 2 (a directed edit with an approved failing
test); a Level 3-4 slice used to wait for the capped capable builder. This module is the missing step in between:

* a *plan* is a short list of Level 1 subtasks (new module, signature, literal examples), their dependencies, and a map
  from each of the parent slice's acceptance criteria to the subtasks that deliver it;
* the plan is proposed by the local model (``propose`` / ``scripts/iron_god_decompose_author.py``, one bounded job, no
  Claude, no ChatGPT) and is **accepted only by deterministic validation** (``validate_plan``): scope inside the parent's
  allowed paths, forbidden paths untouched, new modules only, examples that parse as literals and bind to the signature,
  no contradictions, an acyclic bounded dependency order, every non-gate acceptance criterion covered, and nothing left
  "residual". A slice that cannot be fully covered by Level 1 subtasks is **not** decomposed: the plan is BLOCKED with the
  exact reason and the slice stays with the stronger builder - nothing is ever simplified or silently dropped;
* each subtask becomes an ordinary request (``child_requests``) that enters the unchanged pipeline: routing, the local
  drafter, strict admission, the product worker, canonical preflight, publication, CI, merge. Subtask ids are
  ``<parent>-tN`` so the lane's own slice status, attempt limits, failed-source protection and re-authoring apply to
  each subtask unchanged;
* the parent slice is held (``active_parent_ids``) so no second builder works on it, and is complete only when every
  subtask is (``settle``), at which point the existing progress step records it in the roadmap graph with the
  subtasks' merge evidence.

The model's examples are not an independent oracle. The plan records ``oracle: SELF_AUTHORED`` and the unchanged
gates (worker re-derivation, canonical preflight, repository CI, review) remain the verification; a plan never lowers
them. Everything here is local and deterministic apart from the single model call.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from . import iron_god_local_drafter as drafter
from . import iron_god_roadmap_refill as refill
from .state_io import atomic_json

PLANS = "decompositions"
JOBS = "decompose-jobs"
RESULTS = "decompose-results"
REQUEST_COPIES = "decompose-requests"
MAX_SUBTASKS = 5
MAX_DEPENDENCIES = 2
MAX_PLAN_ATTEMPTS = 2
JOB_TIMEOUT_S = 40 * 60
MIN_EXAMPLES = drafter.MIN_ROADMAP_EXAMPLES
INTERRUPTED_MARK = drafter.INTERRUPTED_MARK
# Acceptance lines every subtask already inherits from the pipeline (they are gates, not behaviour to cover).
GATE_LINE = re.compile(
    r"preflight|required ci|repository ci|review (must )?pass|no gate may be weakened",
    re.IGNORECASE,
)
KEY = re.compile(r"^t([1-9])$")
SIGNATURE = re.compile(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def is_gate_line(text: str) -> bool:
    return bool(GATE_LINE.search(text))


def decomposable(request: dict[str, Any]) -> bool:
    """Only an authorized, executable roadmap slice routed to the stronger builder because of its size is decomposed.

    Planning tasks, escalations, repositories nobody can build, undeclared levels and Level 1-2 slices are not.
    """
    route = request.get("route") or {}
    level = request.get("capability_level")
    return (
        route.get("builder") == refill.CAPABLE
        and type(level) is int
        and level >= 3
        and not request.get("force_builder")
        and request.get("slice") != refill.PLANNING_SLICE
        and not str(request.get("id", "")).endswith(refill.ESCALATION_SUFFIX)
        and bool(request.get("allowed_paths"))
        and bool(request.get("slice_acceptance"))
    )


def _under(path: str, patterns: list[str]) -> bool:
    return refill._matches(path, patterns)


def _normal_path(path: Any) -> str | None:
    if (
        not isinstance(path, str)
        or not path.endswith(".py")
        or "\\" in path
        or path.startswith("/")
    ):
        return None
    parts = path.split("/")
    if any(part in {"", ".", ".."} or ":" in part for part in parts):
        return None
    return path


def _example_problems(sub: dict[str, Any]) -> list[str]:
    """Literal, well-formed, mutually consistent examples that bind to the declared signature."""
    key = sub.get("key")
    raw = sub.get("acceptance_examples")
    if not isinstance(raw, list):
        return [f"{key}: acceptance_examples must be a list"]
    usable = drafter.roadmap_examples({"acceptance_examples": raw})
    problems = []
    if len(usable) != len(raw):
        problems.append(
            f"{key}: every example needs a literal '{{fn}}(...)' call, an 'expected' literal and a 'why'"
        )
    if len(usable) < MIN_EXAMPLES:
        problems.append(
            f"{key}: at least {MIN_EXAMPLES} usable acceptance examples are required"
        )
    seen: dict[str, Any] = {}
    for example in usable:
        if example["call"] in seen and seen[example["call"]] != example["expected"]:
            problems.append(
                f"{key}: contradictory expected values for {example['call']}"
            )
        seen[example["call"]] = example["expected"]
    try:
        stub = ast.parse(sub["signature"].rstrip() + "\n    ...").body[0]
        arguments = stub.args
    except (SyntaxError, IndexError, AttributeError, KeyError):
        return [*problems, f"{key}: signature is not a valid def line"]
    positional = [a.arg for a in [*arguments.posonlyargs, *arguments.args]]
    required = len(positional) - len(arguments.defaults)
    accepted = {*positional, *(a.arg for a in arguments.kwonlyargs)}
    for example in usable:
        call = ast.parse(example["call"].replace("{fn}", "fn", 1), mode="eval").body
        names = [k.arg for k in call.keywords]
        if (
            (len(call.args) > len(positional) and not arguments.vararg)
            or any(name not in accepted for name in names if name)
            or (
                len(call.args) + len([n for n in names if n in positional]) < required
                and not any(k.arg is None for k in call.keywords)
            )
        ):
            problems.append(
                f"{key}: {example['call']} does not bind to {sub['signature']}"
            )
    return problems


def validate_plan(
    raw: Any, request: dict[str, Any], *, exists: Callable[[str], bool]
) -> tuple[dict[str, Any] | None, list[str]]:
    """(normalized plan, []) when the proposal is acceptable, else (None, every reason it is not)."""
    if not isinstance(raw, dict):
        return None, ["PLAN_NOT_AN_OBJECT"]
    problems: list[str] = []
    residual = raw.get("residual")
    if not isinstance(residual, list):
        problems.append(
            "residual must be a list (empty when Level 1 subtasks cover the whole slice)"
        )
    elif residual:
        problems.append(
            "RESIDUAL_NOT_LOCAL: " + "; ".join(str(r)[:160] for r in residual[:3])
        )
    subs = raw.get("subtasks")
    if not isinstance(subs, list) or not 1 <= len(subs) <= MAX_SUBTASKS:
        return None, [*problems, f"subtasks must be a list of 1..{MAX_SUBTASKS} items"]
    allowed, forbidden = (
        list(request["allowed_paths"]),
        list(request.get("forbidden_paths") or []),
    )
    keys: list[str] = []
    modules: set[str] = set()
    clean: list[dict[str, Any]] = []
    for index, sub in enumerate(subs, start=1):
        if not isinstance(sub, dict):
            problems.append(f"t{index}: subtask must be an object")
            continue
        key = sub.get("key")
        if key != f"t{index}" or not KEY.match(str(key)):
            problems.append(f"subtask {index}: key must be exactly 't{index}'")
            continue
        texts = ("title", "module_path", "function_name", "signature", "done_when")
        if any(
            not isinstance(sub.get(name), str) or not sub[name].strip()
            for name in texts
        ):
            problems.append(f"{key}: {', '.join(texts)} must be non-empty strings")
            continue
        path = _normal_path(sub["module_path"])
        name, signature = sub["function_name"], sub["signature"].strip()
        if path is None:
            problems.append(f"{key}: module_path must be a relative POSIX .py path")
        else:
            if not _under(path, allowed) or _under(path, forbidden):
                problems.append(
                    f"{key}: {path} is outside the slice's allowed paths or inside a forbidden path"
                )
            if exists(path):
                problems.append(
                    f"{key}: {path} already exists (Level 1 subtasks add new modules only)"
                )
            if path in modules:
                problems.append(
                    f"{key}: {path} is used by another subtask (one function per new module)"
                )
            modules.add(path)
        match = SIGNATURE.match(signature)
        if (
            not name.isidentifier()
            or name.startswith("_")
            or not match
            or match.group(1) != name
        ):
            problems.append(
                f"{key}: signature must be a 'def {name}(...)' line for a public function"
            )
        deps = sub.get("depends_on", [])
        if (
            not isinstance(deps, list)
            or len(deps) > MAX_DEPENDENCIES
            or any(d not in keys for d in deps)
            or len(set(deps)) != len(deps)
        ):
            problems.append(
                f"{key}: depends_on must name up to {MAX_DEPENDENCIES} EARLIER subtasks"
            )
            deps = []
        problems += _example_problems({**sub, "key": key})
        keys.append(key)
        clean.append(
            {
                "key": key,
                "title": sub["title"].strip()[:120],
                "depends_on": list(deps),
                "module_path": path or "",
                "function_name": name,
                "signature": signature,
                "done_when": sub["done_when"].strip()[:400],
                "acceptance_examples": drafter.roadmap_examples(
                    {"acceptance_examples": sub.get("acceptance_examples") or []}
                ),
            }
        )
    coverage = raw.get("coverage")
    criteria = [str(c) for c in request["slice_acceptance"]]
    behaviour = [i for i, text in enumerate(criteria) if not is_gate_line(text)]
    mapped: dict[str, list[str]] = {}
    if not isinstance(coverage, dict):
        problems.append(
            "coverage must map each acceptance criterion number to the subtask keys that deliver it"
        )
    else:
        for index in behaviour:
            owners = coverage.get(str(index + 1))
            if (
                not isinstance(owners, list)
                or not owners
                or any(o not in keys for o in owners)
            ):
                problems.append(
                    f"acceptance criterion {index + 1} is not covered by existing subtasks: {criteria[index][:120]}"
                )
            else:
                mapped[str(index + 1)] = list(owners)
        used = {o for owners in mapped.values() for o in owners}
        for key in keys:
            if key not in used:
                problems.append(
                    f"{key} delivers no acceptance criterion (a subtask must be traceable to the slice)"
                )
    if problems:
        return None, problems
    return {"subtasks": clean, "coverage": mapped, "oracle": "SELF_AUTHORED"}, []


def planning_prompt(request: dict[str, Any], brief: str, feedback: str = "") -> str:
    """The single, small prompt (the host handles ~900-token prompts; measured, see azathoth-local-model-capability)."""
    criteria = "\n".join(
        f"{i}. {c}" for i, c in enumerate(request["slice_acceptance"], start=1)
    )
    return (
        "You split ONE software slice into small independent subtasks. Each subtask adds ONE NEW Python module "
        "containing ONE pure function (no I/O, no globals) that a junior can write from its signature and examples.\n"
        f"SLICE: {request['objective'][:900]}\nACCEPTANCE CRITERIA:\n{criteria}\n"
        f"New modules must match one of: {', '.join(request['allowed_paths'])}\n"
        f"Never touch: {', '.join(request.get('forbidden_paths') or []) or '(none)'}\nREPOSITORY BRIEF:\n{brief[:700]}\n"
        f"Rules: at most {MAX_SUBTASKS} subtasks keyed t1,t2,... in order; depends_on lists EARLIER keys only; every "
        f"subtask has >= {MIN_EXAMPLES} examples with a literal call using {{fn}} for the function name, an exact "
        "'expected' JSON value and a 'why'; coverage maps every non-gate acceptance criterion NUMBER to the subtask "
        "keys that deliver it. Anything a pure new-module function cannot deliver goes in 'residual' (a non-empty "
        "residual means the slice is NOT decomposed; never hide work).\n"
        'Answer JSON only: {"subtasks":[{"key":"t1","title":"...","depends_on":[],"module_path":"...","function_name":"...",'
        '"signature":"def f(x: str) -> str","done_when":"...","acceptance_examples":[{"call":"{fn}(\'a\')","expected":"A",'
        '"why":"..."}]}],"coverage":{"1":["t1"]},"residual":[]}'
        + (f"\nYour previous plan was rejected: {feedback[:500]}" if feedback else "")
    )


def propose(
    request: dict[str, Any],
    brief: str,
    ask: Callable[[str], Any],
    *,
    exists: Callable[[str], bool],
    attempts: int = MAX_PLAN_ATTEMPTS,
) -> dict[str, Any]:
    """Ask the local model up to ``attempts`` times; the first plan that validates wins. Never raises on a bad answer."""
    feedback, trail = "", []
    for number in range(1, attempts + 1):
        try:
            raw = ask(planning_prompt(request, brief, feedback))
        except Exception as exc:  # an unreachable model is a blocker to record, not a reason to crash the lane
            return {
                "ok": False,
                "reason": f"MODEL_UNAVAILABLE: {type(exc).__name__}: {str(exc)[:160]}",
                "trail": trail,
            }
        plan, problems = validate_plan(raw, request, exists=exists)
        trail.append({"attempt": number, "problems": problems})
        if plan is not None:
            return {"ok": True, "plan": plan, "raw": raw, "trail": trail}
        feedback = "; ".join(problems)[:500]
    return {
        "ok": False,
        "reason": "PLAN_REJECTED: " + trail[-1]["problems"][0][:240],
        "trail": trail,
    }


# ---- persistence ----------------------------------------------------------------------------------------------------


def plan_path(lane_root: Path, parent_id: str) -> Path:
    return Path(lane_root) / PLANS / f"{parent_id}.json"


def load_plans(lane_root: Path) -> list[dict[str, Any]]:
    plans = []
    for path in sorted((Path(lane_root) / PLANS).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(data, dict)
            and data.get("parent_id")
            and isinstance(data.get("subtasks"), list)
        ):
            plans.append(data)
    return plans


def active_parent_ids(lane_root: Path) -> frozenset[str]:
    """Parents held by a live local plan: no stronger builder is dispatched for them while subtasks are in flight."""
    return frozenset(
        p["parent_id"] for p in load_plans(lane_root) if p.get("status") == "ACTIVE"
    )


def done_parent_ids(lane_root: Path) -> frozenset[str]:
    return frozenset(
        p["parent_id"] for p in load_plans(lane_root) if p.get("status") == "DONE"
    )


def children_of(lane_root: Path, parent_id: str) -> list[str]:
    plan = next((p for p in load_plans(lane_root) if p["parent_id"] == parent_id), None)
    return [f"{parent_id}-{s['key']}" for s in plan["subtasks"]] if plan else []


def write_plan(
    lane_root: Path, request: dict[str, Any], plan: dict[str, Any], *, planner: str
) -> dict[str, Any]:
    record = {
        "schema_version": 1,
        "parent_id": request["id"],
        "repository": request["repository"],
        "roadmap_ref": request["roadmap_ref"],
        "capability": request["capability"],
        "slice": request["slice"],
        "capability_level": request.get("capability_level"),
        "objective": request["objective"],
        "slice_acceptance": list(request["slice_acceptance"]),
        "allowed_paths": list(request["allowed_paths"]),
        "forbidden_paths": list(request.get("forbidden_paths") or []),
        "graph_path": request.get("graph_path", ""),
        "planned_at_head": request.get("exact_head", ""),
        "created_at": _now(),
        "status": "ACTIVE",
        "planner": planner,
        "oracle": plan["oracle"],
        "coverage": plan["coverage"],
        "subtasks": plan["subtasks"],
    }
    atomic_json(plan_path(lane_root, request["id"]), record)
    return record


def block(
    lane_root: Path,
    request: dict[str, Any],
    reason: str,
    trail: list[Any] | None = None,
) -> dict[str, Any]:
    """Record the exact blocker; a BLOCKED parent is never re-planned automatically and stays with the stronger builder."""
    record = {
        "schema_version": 1,
        "parent_id": request["id"],
        "repository": request["repository"],
        "subtasks": [],
        "status": "BLOCKED",
        "blocker": reason[:600],
        "created_at": _now(),
        "trail": trail or [],
    }
    atomic_json(plan_path(lane_root, request["id"]), record)
    return record


# ---- subtasks as ordinary requests ------------------------------------------------------------------------------------


def _child_request(
    plan: dict[str, Any],
    sub: dict[str, Any],
    head: str,
    ident: str,
    failures: list[Any],
) -> dict[str, Any]:
    tests = [p for p in plan["allowed_paths"] if "test" in p.lower()]
    objective = (
        f"Add {sub['module_path']} with one pure function: {sub['signature']}. {sub['title']}. "
        f"Done when: {sub['done_when']} (subtask {sub['key']} of roadmap slice {plan['slice']}: {plan['objective'][:200]})"
    )
    return {
        "schema_version": 1,
        "id": ident,
        "repository": plan["repository"],
        "exact_head": head,
        "roadmap_ref": f"{plan['roadmap_ref']}.{sub['key']}",
        "capability": plan["capability"],
        "slice": f"{plan['slice']}.{sub['key']}",
        "objective": objective[:600],
        "slice_acceptance": [
            sub["done_when"],
            *[
                f"{e['call'].replace('{fn}', sub['function_name'])} returns {e['expected']!r}"
                for e in sub["acceptance_examples"]
            ],
        ][:8],
        "completion_evidence": [sub["done_when"]],
        "allowed_paths": [sub["module_path"], *tests],
        "forbidden_paths": list(plan["forbidden_paths"]),
        "runtime_requirements": [],
        "runner_requirements": ["code", "local", "offline"],
        "prior_failures": failures,
        "capability_level": 1,
        "acceptance_examples": sub["acceptance_examples"],
        "directed_edit": None,
        "max_builder_level": 2,
        "graph_path": plan.get("graph_path", ""),
        "decomposition": {
            "parent": plan["parent_id"],
            "key": sub["key"],
            "oracle": plan.get("oracle"),
        },
        "needs": "a backlog item (level 1 build spec) whose id and roadmap_ref equal this request's",
    }


def child_requests(
    lane_root: Path,
    repository: str,
    head: str,
    items: list[dict[str, Any]],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Requests for every ready subtask of this repository's active plans (dependencies merged, none in flight)."""
    out = []
    for plan in load_plans(lane_root):
        if plan.get("status") != "ACTIVE" or plan.get("repository") != repository:
            continue
        status_of = {}
        for sub in plan["subtasks"]:
            base = f"{plan['parent_id']}-{sub['key']}"
            status_of[sub["key"]] = (base, *refill._slice_status(base, items, state))
        for sub in plan["subtasks"]:
            base, status, ident = status_of[sub["key"]]
            if status in {"DONE", "PENDING", "EXHAUSTED"} or any(
                status_of[d][1] != "DONE" for d in sub["depends_on"]
            ):
                continue
            failures = (
                [
                    {
                        "id": item["id"],
                        "outcome": state.get("items", {})
                        .get(item["id"], {})
                        .get("outcome"),
                    }
                    for item in refill.revisions(base, items)
                ]
                if status == "FAILED"
                else []
            )
            out.append(_child_request(plan, sub, head, ident, failures))
    return out


def settle(
    lane_root: Path, items: list[dict[str, Any]], state: dict[str, Any]
) -> list[dict[str, Any]]:
    """Advance each ACTIVE plan: DONE when every subtask is merged, BLOCKED when one has exhausted its attempts."""
    changes = []
    for plan in load_plans(lane_root):
        if plan.get("status") != "ACTIVE":
            continue
        statuses = {
            s["key"]: refill._slice_status(
                f"{plan['parent_id']}-{s['key']}", items, state
            )[0]
            for s in plan["subtasks"]
        }
        if all(v == "DONE" for v in statuses.values()):
            plan.update(status="DONE", completed_at=_now())
        elif "EXHAUSTED" in statuses.values():
            key = next(k for k, v in statuses.items() if v == "EXHAUSTED")
            plan.update(
                status="BLOCKED",
                blocker=f"SUBTASK_EXHAUSTED: {key} spent its attempts; the slice returns to the "
                "stronger builder",
                completed_at=_now(),
            )
        else:
            continue
        atomic_json(plan_path(lane_root, plan["parent_id"]), plan)
        changes.append(
            {
                "id": plan["parent_id"],
                "status": plan["status"],
                "repository": plan["repository"],
                "blocker": plan.get("blocker"),
            }
        )
    return changes


# ---- the planning job (one local-model run at a time, like the drafter) -----------------------------------------------


def _job_path(lane_root: Path, ident: str, attempt: int) -> Path:
    return Path(lane_root) / JOBS / f"{ident}.a{attempt}.json"


def _jobs(lane_root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((Path(lane_root) / JOBS).glob("*.json")):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return rows


def planning_in_progress(lane_root: Path, *, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return any(
        r.get("stage") == "PLANNING"
        and now - float(r.get("started_at", 0)) <= JOB_TIMEOUT_S
        for r in _jobs(lane_root)
    )


def spawn_author(
    request_file: Path, clone: Path, base: str, result: Path
) -> int | None:
    import sys

    runner = (
        Path(__file__).resolve().parents[2] / "scripts" / "iron_god_decompose_author.py"
    )
    result.parent.mkdir(parents=True, exist_ok=True)
    with result.with_suffix(".log").open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(runner),
                "--request",
                str(request_file),
                "--clone",
                str(clone),
                "--base",
                base,
                "--result",
                str(result),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    return process.pid


def _exists_at(clone: Path, base: str) -> Callable[[str], bool]:
    def exists(path: str) -> bool:
        return (
            subprocess.run(
                ["git", "-C", str(clone), "cat-file", "-e", f"{base}:{path}"],
                capture_output=True,
                check=False,
                timeout=60,
            ).returncode
            == 0
        )

    return exists


def _finish(
    lane_root: Path,
    job: dict[str, Any],
    release: Callable[[str], None] | None,
    **fields: Any,
) -> None:
    job.update(fields)
    atomic_json(_job_path(lane_root, job["id"], job["attempt"]), job)
    if release is not None and job.get("lease_id"):
        try:
            release(job["lease_id"])
        except Exception:  # a lease that is already gone must not lose the job record
            pass


def harvest(
    lane_root: Path,
    *,
    now: Callable[[], float] = time.time,
    release: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Finish planning jobs: re-validate the proposal here (defence in depth), then write the plan or the blocker."""
    outcomes = []
    for job in [r for r in _jobs(lane_root) if r.get("stage") == "PLANNING"]:
        result_path = Path(job["result"])
        if not result_path.is_file():
            if now() - float(job["started_at"]) > JOB_TIMEOUT_S:
                _finish(
                    lane_root, job, release, stage="FAILED", reason="PLANNING_TIMED_OUT"
                )
                outcomes.append(
                    {
                        "id": job["id"],
                        "status": "FAILED",
                        "reason": "PLANNING_TIMED_OUT",
                    }
                )
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            request = json.loads(Path(job["request_file"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _finish(
                lane_root,
                job,
                release,
                stage="FAILED",
                reason="PLANNING_RESULT_UNREADABLE",
            )
            outcomes.append(
                {
                    "id": job["id"],
                    "status": "FAILED",
                    "reason": "PLANNING_RESULT_UNREADABLE",
                }
            )
            continue
        if not result.get("ok"):
            reason = str(result.get("reason", "PLANNING_FAILED"))[:300]
            interrupted = (
                INTERRUPTED_MARK in reason
            )  # an owner takeover is routine and never spends an attempt
            _finish(
                lane_root,
                job,
                release,
                stage="INTERRUPTED" if interrupted else "FAILED",
                reason=reason,
            )
            outcomes.append(
                {
                    "id": job["id"],
                    "status": "INTERRUPTED" if interrupted else "FAILED",
                    "reason": reason,
                }
            )
            continue
        plan, problems = validate_plan(
            result.get("raw"),
            request,
            exists=_exists_at(Path(job["clone"]), job["base"]),
        )
        if plan is None:
            _finish(
                lane_root,
                job,
                release,
                stage="FAILED",
                reason="PLAN_REJECTED: " + "; ".join(problems)[:300],
            )
            outcomes.append(
                {
                    "id": job["id"],
                    "status": "FAILED",
                    "reason": "PLAN_REJECTED",
                    "problems": problems,
                }
            )
            continue
        write_plan(
            lane_root, request, plan, planner=str(result.get("model") or "local-model")
        )
        _finish(lane_root, job, release, stage="DONE")
        outcomes.append(
            {"id": job["id"], "status": "PLANNED", "subtasks": len(plan["subtasks"])}
        )
    return outcomes


def advance(
    lane_root: Path,
    candidates: list[dict[str, Any]],
    clone: Path,
    base: str,
    *,
    spawn: Callable[[Path, Path, str, Path], int | None] = spawn_author,
    may_start: bool = True,
    now: Callable[[], float] = time.time,
    reserve: Callable[[], str | None] | None = None,
    release: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Harvest finished planning jobs, then start at most one for the first undecomposed candidate."""
    outcomes = harvest(lane_root, now=now, release=release)
    if not may_start or planning_in_progress(lane_root, now=now()):
        return outcomes
    rows = _jobs(lane_root)
    for request in candidates:
        if not decomposable(request) or plan_path(lane_root, request["id"]).is_file():
            continue
        mine = [r for r in rows if r["id"] == request["id"]]
        spent = sum(1 for r in mine if r.get("stage") != "INTERRUPTED")
        if len(mine) - spent >= drafter.MAX_INTERRUPTIONS:
            continue
        if spent >= MAX_PLAN_ATTEMPTS:
            last = next(
                (r.get("reason") for r in reversed(mine) if r.get("reason")),
                "PLANNING_FAILED",
            )
            block(lane_root, request, f"DECOMPOSITION_ATTEMPTS_EXHAUSTED: {last}")
            outcomes.append(
                {"id": request["id"], "status": "BLOCKED", "reason": str(last)[:300]}
            )
            continue
        lease = reserve() if reserve is not None else None
        if reserve is not None and lease is None:
            continue  # the shared local model is in use
        attempt = len(mine)
        request_file = (
            Path(lane_root) / REQUEST_COPIES / f"{request['id']}.a{attempt}.json"
        )
        atomic_json(request_file, request)
        result = Path(lane_root) / RESULTS / f"{request['id']}.a{attempt}.json"
        result.unlink(missing_ok=True)
        try:
            pid = spawn(request_file, clone, base, result)
        except Exception:
            if release is not None and lease:
                release(lease)
            raise
        atomic_json(
            _job_path(lane_root, request["id"], attempt),
            {
                "id": request["id"],
                "repository": request["repository"],
                "attempt": attempt,
                "stage": "PLANNING",
                "started_at": now(),
                "pid": pid,
                "result": str(result),
                "request_file": str(request_file),
                "clone": str(clone),
                "base": base,
                "lease_id": lease,
            },
        )
        outcomes.append(
            {
                "id": request["id"],
                "status": "STARTED",
                "attempt": attempt,
                "repository": request["repository"],
            }
        )
        break
    return outcomes


# ---- staged proposal: small prompts, module paths assembled by code ------------------------------------------------------

MAX_FUNCTION_TRIES = 2
NAME_OK = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


def module_for(request: dict[str, Any], name: str, taken: set[str], exists: Callable[[str], bool]) -> str | None:
    """A new module path for ``name`` inside the slice's allowed code paths (never chosen by the model)."""
    for pattern in request["allowed_paths"]:
        pattern = pattern.replace("\\", "/")
        if pattern.endswith((".md", ".json", ".yml", ".yaml", ".txt")) or "test" in pattern.lower() or pattern.startswith(("docs/", "doc/", "engineering/")):
            continue
        if pattern.endswith("/**") or pattern.endswith("/*"):
            candidate = pattern.rsplit("/", 1)[0] + f"/{name}.py"
        elif "*" in pattern:
            candidate = pattern.replace("**", name).replace("*", name, 1)
        else:
            continue
        candidate = candidate if candidate.endswith(".py") else candidate + ".py"
        if (
            _normal_path(candidate)
            and _under(candidate, request["allowed_paths"])
            and candidate not in taken
            and not _under(candidate, list(request.get("forbidden_paths") or []))
            and not exists(candidate)
        ):
            return candidate
    return None


def outline_prompt(request: dict[str, Any], brief: str, feedback: str = "") -> str:
    criteria = "\n".join(
        f"{i}. {c}" for i, c in enumerate(request["slice_acceptance"], start=1) if not is_gate_line(str(c))
    )
    return (
        f"Split this software slice into at most {MAX_SUBTASKS} small PURE functions (no I/O, no globals), each in its "
        f"own new module, that together deliver every numbered criterion.\nSLICE: {request['objective'][:900]}\n"
        f"CRITERIA:\n{criteria}\n{brief[:500]}\n"
        "If part of the slice cannot be a new pure function (it edits existing code, runs a program, touches files, a UI "
        "or the network), list it in residual instead of inventing a function for it.\n"
        'Answer JSON only: {"functions":[{"name":"snake_case_name","purpose":"one sentence","criteria":[1],'
        '"depends_on":[]}],"residual":[]}' + (f"\nYour previous answer was rejected: {feedback[:300]}" if feedback else "")
    )


def function_prompt(request: dict[str, Any], entry: dict[str, Any], feedback: str = "") -> str:
    return (
        f"Slice context: {request['objective'][:400]}\n"
        f"Write ONE pure Python function named {entry['name']}: {entry['purpose']}\n"
        f"Give its def line with type annotations, a done_when sentence, and {MIN_EXAMPLES} to 3 examples. Each example "
        "is a literal call written with {fn} as the function name, the exact expected JSON value, and a why.\n"
        'Answer JSON only: {"signature":"def name(x: str) -> str","done_when":"...","acceptance_examples":'
        "[{\"call\":\"{fn}('a')\",\"expected\":\"A\",\"why\":\"...\"}]}"
        + (f"\nYour previous answer was rejected: {feedback[:300]}" if feedback else "")
    )


def _outline_entries(outline: Any) -> list[dict[str, Any]] | None:
    entries = outline.get("functions") if isinstance(outline, dict) else None
    if isinstance(entries, list) and entries and all(isinstance(e, dict) for e in entries):
        return entries
    return None


def propose_staged(
    request: dict[str, Any], brief: str, ask: Callable[[str], Any], *, exists: Callable[[str], bool]
) -> dict[str, Any]:
    """Outline, then one small call per function; assemble; validate. Same result shape as ``propose``."""
    trail: list[dict[str, Any]] = []
    try:
        outline = ask(outline_prompt(request, brief))
        entries = _outline_entries(outline)
        if entries is None:
            outline = ask(outline_prompt(request, brief, 'answer {"functions":[...],"residual":[...]} with a function'))
            entries = _outline_entries(outline)
        if entries is None:
            return {"ok": False, "reason": "PLAN_REJECTED: the outline lists no functions", "trail": trail}
        entries = entries[:MAX_SUBTASKS]
        names = [str(e.get("name", "")) for e in entries]
        if len(set(names)) != len(names) or not all(NAME_OK.match(n) for n in names):
            return {"ok": False, "reason": "PLAN_REJECTED: function names must be unique snake_case", "trail": trail}
        residual = outline.get("residual") if isinstance(outline.get("residual"), list) else []
        subtasks: list[dict[str, Any]] = []
        taken: set[str] = set()
        for index, entry in enumerate(entries, start=1):
            name = str(entry["name"])
            path = module_for(request, name, taken, exists)
            if path is None:
                return {"ok": False, "reason": f"PLAN_REJECTED: no free allowed path for {name}", "trail": trail}
            taken.add(path)
            purpose = str(entry.get("purpose", name))[:200]
            detail: Any = {}
            feedback = ""
            for _try in range(MAX_FUNCTION_TRIES):
                detail = ask(function_prompt(request, {"name": name, "purpose": purpose}, feedback))
                if not isinstance(detail, dict):
                    bad = ["answer is not a JSON object"]
                else:
                    probe = {
                        "key": f"t{index}",
                        "signature": str(detail.get("signature", "")),
                        "acceptance_examples": detail.get("acceptance_examples"),
                    }
                    bad = _example_problems(probe)
                    if not SIGNATURE.match(probe["signature"].strip()):
                        bad.append("signature must be a def line")
                trail.append({"function": name, "problems": bad})
                if not bad:
                    break
                feedback = "; ".join(bad)
            detail = detail if isinstance(detail, dict) else {}
            earlier = names[: index - 1]
            wanted = entry.get("depends_on") if isinstance(entry.get("depends_on"), list) else []
            deps = [f"t{names.index(d) + 1}" for d in wanted if d in earlier][:MAX_DEPENDENCIES]
            subtasks.append(
                {
                    "key": f"t{index}",
                    "title": purpose[:120],
                    "depends_on": deps,
                    "module_path": path,
                    "function_name": name,
                    "signature": str(detail.get("signature", "")),
                    "done_when": str(detail.get("done_when") or purpose),
                    "acceptance_examples": detail.get("acceptance_examples") or [],
                }
            )
        coverage: dict[str, list[str]] = {}
        for index, entry in enumerate(entries, start=1):
            numbers = entry.get("criteria") if isinstance(entry.get("criteria"), list) else []
            for number in numbers:
                coverage.setdefault(str(number), []).append(f"t{index}")
        raw = {"subtasks": subtasks, "coverage": coverage, "residual": residual}
    except Exception as exc:  # an unreachable model is a blocker to record, not a reason to crash the lane
        return {"ok": False, "reason": f"MODEL_UNAVAILABLE: {type(exc).__name__}: {str(exc)[:160]}", "trail": trail}
    plan, problems = validate_plan(raw, request, exists=exists)
    trail.append({"assembled": True, "problems": problems})
    if plan is None:
        return {"ok": False, "reason": "PLAN_REJECTED: " + problems[0][:240], "trail": trail, "raw": raw}
    return {"ok": True, "plan": plan, "raw": raw, "trail": trail}
