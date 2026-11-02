"""Let the local builder draft the Level 1 roadmap slices it is measured to build (issue #594).

The refill routes each request by measured capability. A request routed ``LOCAL`` with a declared Level 1 (one new pure
function) needs a *draft* before it can become a backlog item. The planner for that already exists: the product
worker's own planning step and plan validator. ``scripts/iron_god_draft_author.py`` runs them in a separate process for
one request (the roadmap slice rides along as the planner's feedback), and this module:

* manages one persisted job per attempt (``DRAFTING -> DONE | FAILED``) - one at a time, only while the lane is idle,
  holding the shared runtime lease for the job's duration, at most ``MAX_ATTEMPTS`` per request, a hung run failed by
  age. Results are harvested globally from the job's own stored request, never against whichever repository happens to
  be refilled;
* converts a validated plan into an ordinary backlog draft (``plan_to_draft``) and writes it to the drafts directory,
  where the unchanged strict admission (contract, roadmap reference, scope, route and declared level, baseline,
  reservations, duplicates) accepts or rejects it;
* feeds an admission rejection back to the next attempt's prompt.

A model cannot certify its own work. Structural validity says nothing about whether the function does what the roadmap
asked, so a slice is drafted locally only if the roadmap side supplies independent ``acceptance_examples`` (literal
calls with ``{fn}`` standing for the function name, exact expected values). They are injected into the build spec as
non-negotiable examples, so the builder's tests - and the merge that marks the slice complete - are anchored in the
owner-approved behavior, and a plan whose own examples contradict them is rejected. A slice without such examples is not
drafted locally: it records what is missing and waits for an external draft or a stronger builder.

Level 2 slices are directed edits of existing code. The owner's graph declares the edit (``directed_edit``: the module,
the named units, the approved failing test and its path); the draft is assembled mechanically from that declaration -
no model is involved - and the draft author executes the approved test at the exact base commit and accepts the draft
only if it FAILS there for the right reason (an assertion, not an import or collection error). The unchanged worker then
re-checks that before it calls the model, builds the edit, and requires the same test to pass plus the repository's
canonical preflight. A Level 2 slice is never simplified into Level 1.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import iron_god_roadmap_refill as refill
from .state_io import atomic_json

JOBS = "draft-jobs"
RESULTS = "draft-results"
REQUEST_COPIES = "draft-requests"
MAX_ATTEMPTS = 2
DRAFT_TIMEOUT_S = 30 * 60
SUPPORTED_LEVEL = 1
MIN_ROADMAP_EXAMPLES = 2
ROADMAP_OBJECTIVE_CHARS = 160  # the worker re-validates a build objective at 600 characters


class ContradictsRoadmap(ValueError):
    """The plan's own examples disagree with the roadmap's acceptance examples."""


def _jobs(lane_root: Path) -> list[dict[str, Any]]:
    directory = Path(lane_root) / JOBS
    rows = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and "id" in row:
            rows.append(row)
    return rows


def _job_path(lane_root: Path, ident: str, attempt: int) -> Path:
    return Path(lane_root) / JOBS / f"{ident}.a{attempt}.json"


def drafting_in_progress(lane_root: Path, *, now: float | None = None) -> bool:
    """True while a draft job is running; the lane holds new dispatch so the single local model is never shared."""
    now = time.time() if now is None else now
    return any(row["stage"] == "DRAFTING" and now - float(row.get("started_at", 0)) < DRAFT_TIMEOUT_S + 60
               for row in _jobs(lane_root))


def roadmap_examples(request: dict[str, Any]) -> list[dict[str, Any]]:
    """The slice's independent acceptance examples that are well formed: a ``{fn}(...)`` call with literal arguments."""
    usable = []
    for example in request.get("acceptance_examples") or []:
        if not isinstance(example, dict) or "expected" not in example or not str(example.get("why", "")).strip():
            continue
        call = " ".join(str(example.get("call", "")).split())
        if not call.startswith("{fn}("):
            continue
        try:
            tree = ast.parse(call.replace("{fn}", "fn", 1), mode="eval").body
            if not (isinstance(tree, ast.Call) and isinstance(tree.func, ast.Name)):
                continue
            for arg in [*tree.args, *(k.value for k in tree.keywords)]:
                ast.literal_eval(arg)
            ast.literal_eval(repr(example["expected"]))
        except (SyntaxError, ValueError):
            continue
        usable.append({"call": call, "expected": example["expected"], "why": " ".join(str(example["why"]).split())})
    return usable


DIRECTED_LEVEL = 2
DIRECTED_UNITS = 3  # the worker bounds a directed edit at a few units; more is a decomposition problem


def directed_spec(request: dict[str, Any]) -> dict[str, Any] | None:
    """The owner's declared directed edit when it is complete and well formed, else None."""
    spec = request.get("directed_edit")
    if not isinstance(spec, dict):
        return None
    texts = ("module_path", "test_path", "test_source")
    targets = spec.get("targets")
    if (any(not isinstance(spec.get(name), str) or not spec[name].strip() for name in texts)
            or not isinstance(targets, list) or not targets or len(targets) > DIRECTED_UNITS
            or any(not isinstance(t, str) or not t.isidentifier() for t in targets)):
        return None
    try:
        tree = ast.parse(spec["test_source"])
    except SyntaxError:
        return None
    if not any(isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in tree.body):
        return None
    mentioned = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    if not mentioned & set(targets):
        return None  # a test that never touches a named unit cannot approve an edit of it
    if not any(isinstance(n, ast.Assert) for n in ast.walk(tree)) and "raises" not in spec["test_source"]:
        return None
    return spec


def eligible(request: dict[str, Any]) -> bool:
    route = request.get("route") or {}
    if route.get("builder") != refill.LOCAL:
        return False
    if request.get("capability_level") == DIRECTED_LEVEL:
        return directed_spec(request) is not None
    return request.get("capability_level") == SUPPORTED_LEVEL and len(roadmap_examples(request)) >= MIN_ROADMAP_EXAMPLES


def missing_directed_edit(request: dict[str, Any]) -> bool:
    """A LOCAL Level 2 request that could be built locally but for the owner's declared directed edit and approved test."""
    route = request.get("route") or {}
    return (route.get("builder") == refill.LOCAL and request.get("capability_level") == DIRECTED_LEVEL
            and directed_spec(request) is None)


def missing_examples(request: dict[str, Any]) -> bool:
    """A LOCAL Level 1 request that could be drafted locally but for the roadmap-side acceptance examples."""
    route = request.get("route") or {}
    return (route.get("builder") == refill.LOCAL and request.get("capability_level") == SUPPORTED_LEVEL
            and len(roadmap_examples(request)) < MIN_ROADMAP_EXAMPLES)


def direct_to_draft(request: dict[str, Any], validated: dict[str, Any]) -> dict[str, Any]:
    """A backlog draft (Level 2 directed edit) from the owner's declared edit that the worker's validator accepted."""
    slice_text = f"roadmap slice {request['slice']} of capability {request['capability']}"
    direction = validated["direction"]
    objective = " ".join(str(request["objective"]).split())[:ROADMAP_OBJECTIVE_CHARS]
    return {
        "id": request["id"], "repository": request["repository"],
        "title": f"{', '.join(validated['targets'])} for {request['slice']}"[:120],
        "roadmap_ref": request["roadmap_ref"], "addresses": f"{request['capability']}: {request['slice']}",
        "why": f"The owner-approved {slice_text} requires it: {request['objective']}"[:1000],
        "consumer": f"the {slice_text} and the product code that builds on it ({request['objective'][:100]})",
        "objective": f"{direction} Roadmap slice goal: {objective}"[:600],
        "interfaces": [validated["module_path"]], "depends_on": [],
        "acceptance_criteria": [*request.get("slice_acceptance", [])][:8] or [direction],
        "tests": [validated["test_path"]], "level": DIRECTED_LEVEL, "complexity": "low",
        "verification": "the owner-approved test fails at the exact base, passes after the edit; canonical preflight and CI",
        "definition_of_done": "merged with green CI and the roadmap graph updated",
        "approved_base": validated["approved_base"],
        "direct": {"module_path": validated["module_path"], "targets": validated["targets"],
                   "approved_base": validated["approved_base"],  # the worker reads it from here, not from the item
                   "direction": direction, "test_path": validated["test_path"],
                   "test_source": validated["test_source"],
                   **({"add": validated["add"]} if validated.get("add") else {})},
    }


def plan_to_draft(request: dict[str, Any], raw: dict[str, Any], validated: dict[str, Any]) -> dict[str, Any]:
    """A backlog draft (Level 1 build spec) from a plan the worker's validator accepted.

    The roadmap's acceptance examples are injected for the chosen function name and always win: a plan example with the
    same call but a different expected value contradicts the roadmap and raises ``ContradictsRoadmap``.
    """
    name = validated["function_name"]
    given = {" ".join(call.split()): example for example in roadmap_examples(request)
             for call in [example["call"].replace("{fn}", name)]}
    usable = {" ".join(str(e["call"]).split()) for e in validated["examples"]}
    own = []
    for e in raw.get("examples", []):
        if not (isinstance(e, dict) and "call" in e and "expected" in e):
            continue
        call = " ".join(str(e["call"]).split())
        if call not in usable:
            continue
        if call in given and given[call]["expected"] != e["expected"]:
            raise ContradictsRoadmap(f"{call} expected {e['expected']!r}, roadmap says {given[call]['expected']!r}")
        if call not in given:
            own.append({"call": call, "why": " ".join(str(e.get("why", "")).split()), "expected": e["expected"]})
    injected = [{"call": call, "why": "roadmap acceptance example: " + example["why"], "expected": example["expected"]}
                for call, example in given.items()]
    examples = [*injected, *own]
    criteria = [*validated["acceptance_criteria"], *[f"{e['call']} returns {e['expected']!r} (roadmap acceptance)"
                                                      for e in injected]]
    roadmap_part = " ".join(str(request["objective"]).split())[:ROADMAP_OBJECTIVE_CHARS]
    objective = f"{validated['objective']} Roadmap slice goal: {roadmap_part}"[:600]
    slice_text = f"roadmap slice {request['slice']} of capability {request['capability']}"
    return {
        "id": request["id"], "repository": request["repository"],
        "title": f"{name} for {request['slice']}"[:120],
        "roadmap_ref": request["roadmap_ref"], "addresses": f"{request['capability']}: {request['slice']}",
        "why": f"The owner-approved {slice_text} requires it: {request['objective']}"[:1000],
        "consumer": f"the {slice_text} and the product code that builds on it ({request['objective'][:100]})",
        "objective": objective, "interfaces": [validated["module_path"]], "depends_on": [],
        "acceptance_criteria": criteria, "tests": [validated["test_path"]],
        "level": SUPPORTED_LEVEL, "complexity": "low",
        "verification": "canonical preflight and repository CI at the exact head",
        "definition_of_done": "merged with green CI and the roadmap graph updated",
        "build": {"module_path": validated["module_path"], "function_name": name,
                  "signature": validated["signature"], "objective": objective, "examples": examples,
                  "note": validated.get("note", "")},
    }


def _rejection_feedback(lane_root: Path, ident: str) -> str:
    rows = [e for e in refill.load_store(lane_root)["ledger"] if e["kind"] == "REJECTED" and e["id"] == ident]
    return ", ".join(rows[-1]["detail"].get("problems", [])) if rows else ""


def _draft_state(lane_root: Path, ident: str) -> str:
    """NONE, UNJUDGED (written, not yet admitted or rejected), or JUDGED (rejected; a retry may follow)."""
    path = Path(lane_root) / refill.DRAFTS / f"{ident}.json"
    if not path.is_file():
        return "NONE"
    judged = refill.load_store(lane_root)["processed"].get(ident)
    return "JUDGED" if judged == hashlib.sha256(path.read_bytes()).hexdigest() else "UNJUDGED"


def has_result_ready(lane_root: Path) -> bool:
    """A draft job whose result file has appeared: the next pass should harvest it without waiting for the interval."""
    return any(row["stage"] == "DRAFTING" and Path(row["result"]).is_file() for row in _jobs(lane_root))


def spawn_author(request_file: Path, clone: Path, base: str, result: Path) -> int | None:
    """Default launcher; the lane supplies a release-bound variant."""
    runner = Path(__file__).resolve().parents[2] / "scripts" / "iron_god_draft_author.py"
    result.parent.mkdir(parents=True, exist_ok=True)
    with result.with_suffix(".log").open("ab") as log:
        process = subprocess.Popen([sys.executable, str(runner), "--request", str(request_file), "--clone", str(clone),
                                    "--base", base, "--result", str(result)], stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL)
    return process.pid


def _finish(lane_root: Path, job: dict[str, Any], release: Callable[[str], None] | None, **fields: Any) -> None:
    job.update(fields)
    atomic_json(_job_path(lane_root, job["id"], job["attempt"]), job)
    if release is not None and job.get("lease_id"):
        try:
            release(job["lease_id"])
        except Exception:  # a lease that is already gone is not a reason to lose the job record
            pass


def harvest(lane_root: Path, *, now: Callable[[], float] = time.time,
            release: Callable[[str], None] | None = None) -> list[dict[str, Any]]:
    """Finish every draft job whose result is in (or that hung), from the job's OWN stored request.

    Global on purpose: a job belongs to its repository's request, not to whichever repository is being refilled, and
    the strict admission still checks the draft against the then-current request, so a stale draft is simply rejected.
    """
    outcomes: list[dict[str, Any]] = []
    for job in [row for row in _jobs(lane_root) if row["stage"] == "DRAFTING"]:
        result_path = Path(job["result"])
        if not result_path.is_file():
            if now() - float(job["started_at"]) > DRAFT_TIMEOUT_S:
                _finish(lane_root, job, release, stage="FAILED", reason="DRAFT_TIMED_OUT")
                outcomes.append({"id": job["id"], "status": "FAILED", "reason": "DRAFT_TIMED_OUT",
                                 "repository": job.get("repository")})
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            request = json.loads(Path(job["request_file"]).read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            _finish(lane_root, job, release, stage="FAILED", reason="DRAFT_RESULT_UNREADABLE")
            outcomes.append({"id": job["id"], "status": "FAILED", "reason": "DRAFT_RESULT_UNREADABLE",
                             "repository": job.get("repository")})
            continue
        if not result.get("ok"):
            reason = str(result.get("reason", "DRAFT_FAILED"))[:300]
            _finish(lane_root, job, release, stage="FAILED", reason=reason)
            outcomes.append({"id": job["id"], "status": "FAILED", "reason": reason, "repository": job.get("repository")})
            continue
        try:
            draft = (direct_to_draft(request, result["validated"]) if request.get("capability_level") == DIRECTED_LEVEL
                     else plan_to_draft(request, result["plan"], result["validated"]))
        except (ContradictsRoadmap, KeyError, TypeError, ValueError) as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:240]}"
            _finish(lane_root, job, release, stage="FAILED", reason=reason)
            outcomes.append({"id": job["id"], "status": "FAILED", "reason": reason, "repository": job.get("repository")})
            continue
        target = Path(lane_root) / refill.DRAFTS / f"{job['id']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(target, draft)
        _finish(lane_root, job, release, stage="DONE")
        outcomes.append({"id": job["id"], "status": "DRAFTED", "repository": job.get("repository")})
    return outcomes


def advance(lane_root: Path, requests: list[dict[str, Any]], clone: Path, base: str, *,
            spawn: Callable[[Path, Path, str, Path], int | None] = spawn_author, may_start: bool = True,
            now: Callable[[], float] = time.time, extra: dict[str, Any] | None = None,
            reserve: Callable[[], str | None] | None = None,
            release: Callable[[str], None] | None = None) -> list[dict[str, Any]]:
    """Harvest finished jobs (globally), then start at most one new one for ``requests``; one row per thing that happened.

    ``reserve`` takes the shared runtime lease for the job and returns its id (None means the model is in use: do not
    start); ``release`` gives it back when the job ends.
    """
    outcomes = harvest(lane_root, now=now, release=release)
    for request in requests:
        if missing_directed_edit(request):
            outcomes.append({"id": request["id"], "status": "NEEDS_ACCEPTANCE_EXAMPLES",
                             "reason": f"declare directed_edit on Level 2 slice {request['slice']} (module_path, named "
                                       "targets, test_path and an approved test_source that exercises them) so the local "
                                       "builder can be verified independently"})
        if missing_examples(request):
            outcomes.append({"id": request["id"], "status": "NEEDS_ACCEPTANCE_EXAMPLES",
                             "reason": f"declare at least {MIN_ROADMAP_EXAMPLES} acceptance_examples on slice "
                                       f"{request['slice']} so the local builder can be verified independently"})
    if not may_start or drafting_in_progress(lane_root, now=now()):
        return outcomes
    admitted = {str(item.get("id")) for item in refill.load_store(lane_root)["items"]}
    for request in requests:
        if not eligible(request) or _draft_state(lane_root, request["id"]) == "UNJUDGED":
            continue
        if request["id"] in admitted:
            continue  # a valid draft is already admitted for this request: a second model run only burns RAM and time
        attempt = sum(1 for row in _jobs(lane_root) if row["id"] == request["id"])
        if attempt >= (1 if request.get("capability_level") == DIRECTED_LEVEL else MAX_ATTEMPTS):
            # a directed draft is assembled mechanically: running it again would only repeat the verdict
            continue  # two local attempts failed or were rejected: a stronger builder or a human decides next
        lease = reserve() if reserve is not None else None
        if reserve is not None and lease is None:
            continue  # the shared local model is in use
        guided = {**request, **(extra or {}), "planner_feedback": _rejection_feedback(lane_root, request["id"])}
        request_file = Path(lane_root) / REQUEST_COPIES / f"{request['id']}.a{attempt}.json"
        atomic_json(request_file, guided)
        result = Path(lane_root) / RESULTS / f"{request['id']}.a{attempt}.json"
        result.unlink(missing_ok=True)
        try:
            pid = spawn(request_file, clone, base, result)
        except Exception:
            if release is not None and lease:
                release(lease)
            raise
        atomic_json(_job_path(lane_root, request["id"], attempt),
                    {"id": request["id"], "repository": request["repository"], "attempt": attempt, "stage": "DRAFTING",
                     "started_at": now(), "pid": pid, "result": str(result), "request_file": str(request_file),
                     "base": base, "lease_id": lease})
        outcomes.append({"id": request["id"], "status": "STARTED", "attempt": attempt,
                         "repository": request["repository"]})
        break  # one local-model job at a time
    return outcomes
