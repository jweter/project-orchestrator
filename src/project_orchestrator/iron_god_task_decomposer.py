"""Break an authorized roadmap slice the local builder cannot take into subtasks it can (Level 1), without a stronger model.

The product lane routes every roadmap slice by its declared capability level. The local builder is measured only for
Level 1 (one new pure function with literal acceptance examples) and Level 2 (a directed edit with an approved failing
test); a Level 3-4 slice used to wait for the capped capable builder. This module is the missing step in between:

* a *plan* is a short list of subtasks, their dependencies, and a map from each of the parent slice's acceptance
  criteria to the subtasks that deliver it. A subtask is either Level 1 (``new``: a new module with one pure function and
  literal examples) or Level 2 (``edit``: a directed edit of named units of an EXISTING module, with a test the model
  wrote that the unchanged draft author proves FAILS at the exact base for the right reason before anything is built);
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
from typing import Any, Callable, Iterator

from .iron_god_local_model import is_memory_wait
from . import iron_god_cpp_lane as cpp_lane
from . import iron_god_directed_edit as directed_edit
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
# A rejection no second model sample can cure: the slice's own scope has nowhere for a local subtask to live.
TERMINAL_MARKERS = ("NO_LOCAL_TARGET_IN_SCOPE",)
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]+\.py$|_test\.py$")
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
        and not native_scope(request)
    )


def native_scope(request: dict[str, Any]) -> bool:
    """True when the slice reaches a registered native (C++) source root, e.g. Everward's ``src/simulation/``.

    This decomposer only plans Python steps (a new pure-function module, or a directed edit of a Python module). Found
    live on 2026-11-08: given Everward's C++ ``first-loop-chained-simulation-test`` slice, the 7B invented unrelated
    Python functions, and the module placer would have put ``.py`` files under the C++ ``src/simulation/`` tree. Native
    slices are built by the local C++ lane (which needs a ``cpp`` section in the slice's graph), never decomposed here.
    """
    profile = cpp_lane.profile_for(request.get("repository"))
    if profile is None:
        return False
    for pattern in request.get("allowed_paths") or []:
        base = str(pattern).replace("\\", "/").split("*", 1)[0]
        if any(base.startswith(root) or root.startswith(base) for root in profile.source_roots):
            return True
    return False


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


def normalize_examples(name: Any, examples: Any) -> Any:
    """Replace a leading ``name(`` with ``{fn}(`` in each example call (the model's most common slip, mechanically fixed;
    nothing else about an example is changed, so a wrong example is still rejected)."""
    if not isinstance(examples, list) or not isinstance(name, str) or not name:
        return examples
    out = []
    for example in examples:
        if isinstance(example, dict) and isinstance(example.get("call"), str):
            call = example["call"].strip()
            if call.startswith(name + "(") and not call.startswith("{fn}("):
                example = {**example, "call": "{fn}" + call[len(name):]}
        out.append(example)
    return out


IO_ANNOTATIONS = {"Path", "PurePath", "PathLike", "IO", "BinaryIO", "TextIO", "BufferedReader"}
PLACEHOLDER_PATH = re.compile(r"""['"](?:\./|/)?path/to/""", re.IGNORECASE)
MEDIA_FILE_LITERAL = re.compile(
    r"""['"][^'"\n]*\.(?:wav|mp3|flac|ogg|m4a|aac|mp4|mkv|avi|mov|png|jpe?g|gif|bmp|pdf|psarc|sqlite3?|db|zip|gguf|onnx|pth?)['"]""",
    re.IGNORECASE,
)
PATH_LIKE_PARAMETER = re.compile(r"(?:^|_)(?:path|paths|dir|directory|folder)$", re.IGNORECASE)


def _structured_expected(expected: Any) -> bool:
    """True when the expected value is data a file read would produce (dict/list/tuple/set), not a scalar a name parser returns."""
    if isinstance(expected, str):
        try:
            expected = ast.literal_eval(expected)
        except (SyntaxError, ValueError):
            return False
    return isinstance(expected, (dict, list, tuple, set))


def _no_result(expected: Any) -> bool:
    """None, or the text 'None' (the planner often writes the literal as a string)."""
    return expected is None or (isinstance(expected, str) and expected.strip() == "None")


def _io_problems(key: Any, stub: Any, usable: list[dict[str, Any]]) -> list[str]:
    """Reject a 'pure function' that reads a file: literal examples cannot specify it.

    Measured 2026-11-11: the 7B planned ``analyze_guide_vocal(audio_file_path)`` with ``'path/to/guide_vocal.wav'`` and exact
    F0 values; the worker failed twice because the file does not exist, and no literal example could ever have been right.
    A function that needs a file belongs with the stronger builder (or takes the data itself as its argument).

    The rule needs file-access evidence, not a file name alone, so a pure filename parser such as ``extension('song.wav')``
    is accepted: (1) a Path/stream annotation, (2) a ``path/to/`` placeholder, or (3) a media/archive/model/database file
    literal passed to a path-like parameter (``path``, ``audio_file_path``, ``dir``...) where the expected value is
    structured data a read would produce. Dynamic I/O that matches none of these is still left to the worker's own bounds.
    """
    arguments = stub.args
    parameters = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    for arg in parameters:
        text = ast.unparse(arg.annotation) if arg.annotation is not None else ""
        if any(part in IO_ANNOTATIONS for part in re.findall(r"[A-Za-z_]+", text)):
            return [f"{key}: {stub.name} takes a file or stream ({arg.arg}: {text}); a pure function with literal examples "
                    "cannot be specified against files - pass the data itself"]
    path_like = any(PATH_LIKE_PARAMETER.search(arg.arg) for arg in parameters)
    for example in usable:
        call = str(example["call"])
        if PLACEHOLDER_PATH.search(call) or (
            path_like and MEDIA_FILE_LITERAL.search(call) and _structured_expected(example["expected"])
        ):
            return [f"{key}: {call} names a file that does not exist at the base; a pure function with literal "
                    "examples cannot be specified against files - pass the data itself"]
    if usable and all(_no_result(example["expected"]) for example in usable):
        # Measured 2026-11-13: the 7B planned copy_vendored_fixture(source_path, target_path) with every example expecting
        # None. A function whose only effect is outside its return value cannot be specified by literal examples.
        return [f"{key}: every example of {stub.name} expects None, so the function is specified only by a side effect "
                "(copying, writing, printing); a pure function with literal examples must return the value it computes"]
    return []


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
        if drafter._unresolved_placeholder(example["expected"]):
            problems.append(f"{key}: SELF_AUTHORED_ACCEPTANCE_PLACEHOLDER: "
                            "model-proposed exact output contains unresolved placeholder values")
        seen[example["call"]] = example["expected"]
    try:
        stub = ast.parse(sub["signature"].rstrip() + "\n    ...").body[0]
        arguments = stub.args
    except (SyntaxError, IndexError, AttributeError, KeyError):
        return [*problems, f"{key}: signature is not a valid def line"]
    positional = [a.arg for a in [*arguments.posonlyargs, *arguments.args]]
    required = len(positional) - len(arguments.defaults)
    accepted = {*positional, *(a.arg for a in arguments.kwonlyargs)}
    problems.extend(_io_problems(key, stub, usable))
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


def _edit_subtask(
    sub: dict[str, Any],
    key: str,
    request: dict[str, Any],
    exists: Callable[[str], bool],
    read: Callable[[str], str | None] | None,
    taken_tests: set[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate one Level 2 directed-edit subtask against the repository at the exact base."""
    allowed, forbidden = list(request["allowed_paths"]), list(request.get("forbidden_paths") or [])
    if sub.get("unresolved"):
        return None, [f"{key}: {sub['unresolved']}"]
    texts = ("title", "module_path", "test_path", "test_source", "done_when")
    if any(not isinstance(sub.get(name), str) or not sub[name].strip() for name in texts):
        return None, [f"{key}: {', '.join(texts)} must be non-empty strings for an edit"]
    problems: list[str] = []
    module, test = _normal_path(sub["module_path"]), _normal_path(sub["test_path"])
    if module is None or test is None:
        return None, [f"{key}: module_path and test_path must be relative POSIX .py paths"]
    if module.startswith("tests/") or not _under(module, allowed) or _under(module, forbidden):
        problems.append(f"{key}: {module} is outside the slice's allowed paths, a test file, or forbidden")
    if not test.startswith("tests/") or not test.rsplit("/", 1)[-1].startswith("test_"):
        problems.append(f"{key}: the test must be a new tests/**/test_*.py file")
    if not _under(test, allowed) or _under(test, forbidden):
        problems.append(f"{key}: {test} is outside the slice's allowed paths or forbidden")
    if exists(test) or test in taken_tests:
        problems.append(f"{key}: {test} already exists (an approved test is a new file)")
    taken_tests.add(test)
    targets = sub.get("targets")
    if (
        not isinstance(targets, list)
        or not targets
        or len(targets) > drafter.DIRECTED_UNITS
        or any(not isinstance(t, str) or not t.isidentifier() for t in targets)
    ):
        return None, [*problems, f"{key}: targets must name 1..{drafter.DIRECTED_UNITS} existing units"]
    if read is None:
        return None, [*problems, f"{key}: edit subtasks need repository access to verify the named units"]
    source = read(module) if exists(module) else None
    if source is None:
        return None, [*problems, f"{key}: {module} does not exist at the base (an edit changes existing code)"]
    try:
        found = directed_edit.units(source)
    except SyntaxError:
        return None, [*problems, f"{key}: {module} does not parse"]
    missing = [t for t in targets if t not in found]
    if missing:
        problems.append(f"{key}: units {missing} do not exist in {module}; existing units: {sorted(found)[:15]}")
    declared = {
        "module_path": module,
        "test_path": test,
        "test_source": sub["test_source"],
        "targets": list(targets),
    }
    if drafter.directed_spec({"directed_edit": declared}) is None:
        problems.append(f"{key}: the test must parse, define test_*, assert, and exercise a named unit")
    if len(sub["done_when"].strip()) < 20:
        problems.append(f"{key}: done_when must state the required behaviour (20+ characters)")
    if problems:
        return None, problems
    return {
        "kind": "edit",
        "module_path": module,
        "test_path": test,
        "test_source": sub["test_source"],
        "targets": list(targets),
        "done_when": sub["done_when"].strip()[:600],
        "title": sub["title"].strip()[:120],
    }, []


def validate_plan(
    raw: Any,
    request: dict[str, Any],
    *,
    exists: Callable[[str], bool],
    read: Callable[[str], str | None] | None = None,
    skip_criteria: frozenset[int] = frozenset(),
) -> tuple[dict[str, Any] | None, list[str]]:
    """(normalized plan, []) when the proposal is acceptable, else (None, every reason it is not).

    ``skip_criteria`` (0-based) are criteria already handed to the residual by ``salvage_plan``: not required of these steps.
    """
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
    taken_tests: set[str] = set()
    edited: dict[str, str] = {}
    clean: list[dict[str, Any]] = []
    for index, sub in enumerate(subs, start=1):
        if not isinstance(sub, dict):
            problems.append(f"t{index}: subtask must be an object")
            continue
        key = sub.get("key")
        if key != f"t{index}" or not KEY.match(str(key)):
            problems.append(f"subtask {index}: key must be exactly 't{index}'")
            continue
        if sub.get("kind") == "edit":
            edit, issues = _edit_subtask(sub, key, request, exists, read, taken_tests)
            problems += issues
            deps = sub.get("depends_on", [])
            if (
                not isinstance(deps, list)
                or len(deps) > MAX_DEPENDENCIES
                or any(d not in keys for d in deps)
                or len(set(deps)) != len(deps)
            ):
                problems.append(f"{key}: depends_on must name up to {MAX_DEPENDENCIES} EARLIER subtasks")
                deps = []
            if edit is not None:
                earlier = edited.get(edit["module_path"])
                if earlier is not None and earlier not in deps:
                    problems.append(
                        f"{key}: edits {edit['module_path']} already edited by {earlier}; it must depend on {earlier}"
                    )
                edited[edit["module_path"]] = key
                clean.append({"key": key, **edit, "depends_on": list(deps)})
            keys.append(key)
            continue
        sub = {**sub, "acceptance_examples": normalize_examples(sub.get("function_name"), sub.get("acceptance_examples"))}
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
    behaviour = [i for i, text in enumerate(criteria) if not is_gate_line(text) and i not in skip_criteria]
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


_KEY_PROBLEM = re.compile(r"^(t[1-9])\b")
_UNCOVERED = re.compile(r"^acceptance criterion (\d+) is not covered")


def salvage_plan(
    raw: Any,
    request: dict[str, Any],
    *,
    exists: Callable[[str], bool],
    read: Callable[[str], str | None] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Keep every step that passes the strict validator; hand the rest of the slice to the residual.

    Owner direction 2026-11-08: a small piece of a big slice is useful work even if another slice has to finish it. A plan
    is therefore accepted when at least one step is valid and delivers a criterion. Each kept step is validated exactly as
    strictly as before (``validate_plan``). Steps that fail, and steps depending on them, are dropped with their reasons;
    criteria no kept step delivers, and the model's own residual, become the plan's ``residual``. A plan with a residual is
    never reported complete (see ``settle``): the remainder returns to the stronger builder.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("subtasks"), list) or not isinstance(raw.get("coverage"), dict):
        return validate_plan(raw, request, exists=exists, read=read)
    criteria = [str(c) for c in request["slice_acceptance"]]
    residual = [str(r)[:300] for r in (raw.get("residual") or []) if isinstance(raw.get("residual"), list)]
    subs = [s for s in raw["subtasks"] if isinstance(s, dict)]
    coverage = {str(k): list(v) for k, v in raw["coverage"].items() if isinstance(v, list)}
    skip: set[int] = set()
    lost: set[int] = set()  # criteria that lost one of their owners: part of their work is not planned any more
    dropped: list[dict[str, Any]] = []
    problems: list[str] = []
    for _round in range(2 * MAX_SUBTASKS + 2):
        plan, problems = validate_plan({"subtasks": subs, "coverage": coverage, "residual": []}, request,
                                       exists=exists, read=read, skip_criteria=frozenset(skip))
        if plan is not None:
            plan["residual"] = residual + [criteria[i][:300] for i in sorted(skip | lost)]
            plan["dropped"] = dropped
            return plan, []
        bad = {m.group(1) for m in (_KEY_PROBLEM.match(p) for p in problems) if m}
        uncovered = {int(m.group(1)) - 1 for m in (_UNCOVERED.match(p) for p in problems) if m}
        other = [p for p in problems if not _KEY_PROBLEM.match(p) and not _UNCOVERED.match(p)]
        if other and not bad and not uncovered:
            return None, problems  # a structural problem no dropping can fix
        if not bad and not (uncovered - skip):
            return None, problems
        skip |= {i for i in uncovered if 0 <= i < len(criteria)}
        if bad:
            changed = True
            while changed:  # a step that depends on a dropped step is dropped too
                changed = False
                for sub in subs:
                    if sub.get("key") not in bad and any(d in bad for d in (sub.get("depends_on") or [])):
                        bad.add(sub["key"])
                        changed = True
            for sub in subs:
                if sub.get("key") in bad:
                    dropped.append({"title": str(sub.get("title") or sub.get("function_name") or sub.get("key"))[:120],
                                    "problems": [p[:200] for p in problems if p.startswith(str(sub.get("key")))][:3]})
            for number, owners in coverage.items():  # review P2 on #659: surviving owners do not make it complete
                if any(o in bad for o in owners) and str(number).isdigit() and 0 < int(number) <= len(criteria):
                    lost.add(int(number) - 1)
            kept = [s for s in subs if s.get("key") not in bad]
            renumber = {s["key"]: f"t{i}" for i, s in enumerate(kept, start=1)}
            subs = [{**s, "key": renumber[s["key"]],
                     "depends_on": [renumber[d] for d in (s.get("depends_on") or []) if d in renumber]} for s in kept]
            coverage = {k: [renumber[o] for o in v if o in renumber] for k, v in coverage.items()}
            coverage = {k: v for k, v in coverage.items() if v}
        if not subs:
            return None, problems
    return None, problems


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
        "residual": list(plan.get("residual") or []),
        "dropped": list(plan.get("dropped") or []),
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


def _edit_child_request(
    plan: dict[str, Any], sub: dict[str, Any], head: str, ident: str, failures: list[Any]
) -> dict[str, Any]:
    objective = (
        f"{sub['done_when']} (subtask {sub['key']} of roadmap slice {plan['slice']}: {plan['objective'][:200]})"
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
        "slice_acceptance": [sub["done_when"]],
        "completion_evidence": [sub["done_when"]],
        "allowed_paths": [sub["module_path"], sub["test_path"]],
        "forbidden_paths": list(plan["forbidden_paths"]),
        "runtime_requirements": [],
        "runner_requirements": ["code", "local", "offline"],
        "prior_failures": failures,
        "capability_level": drafter.DIRECTED_LEVEL,
        "acceptance_examples": [],
        "directed_edit": {
            "module_path": sub["module_path"],
            "targets": list(sub["targets"]),
            "test_path": sub["test_path"],
            "test_source": sub["test_source"],
        },
        "max_builder_level": 2,
        "graph_path": plan.get("graph_path", ""),
        "decomposition": {"parent": plan["parent_id"], "key": sub["key"], "oracle": plan.get("oracle")},
        "needs": "a backlog item (level 2 directed edit) whose id and roadmap_ref equal this request's",
    }


def _child_request(
    plan: dict[str, Any],
    sub: dict[str, Any],
    head: str,
    ident: str,
    failures: list[Any],
) -> dict[str, Any]:
    if sub.get("kind") == "edit":
        return _edit_child_request(plan, sub, head, ident, failures)
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
            if (
                status in {"DONE", "PENDING", "EXHAUSTED"}
                or drafting_exhausted(lane_root, ident, directed=sub.get("kind") == "edit")
                or any(status_of[d][1] != "DONE" for d in sub["depends_on"])
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


def local_work_waiting(lane_root: Path, requests: list[dict[str, Any]]) -> bool:
    """True when a LOCAL request could still be drafted: planning never takes the model slot from ready local work."""
    admitted = {str(item.get("id")) for item in refill.load_store(lane_root)["items"]}
    for request in requests:
        if not drafter.eligible(request) or request["id"] in admitted:
            continue
        spent = sum(1 for r in drafter._jobs(lane_root) if r.get("id") == request["id"] and not drafter._interrupted(r))
        if spent < (1 if request.get("capability_level") == drafter.DIRECTED_LEVEL else drafter.MAX_ATTEMPTS):
            return True
    return False


def drafting_exhausted(lane_root: Path, ident: str, *, directed: bool = False) -> bool:
    """True when every local drafting attempt is spent and none is running or awaiting admission.

    A Level 2 directed edit is attempted once (the drafter stops after one), so ``directed`` lowers the limit to match."""
    rows = [r for r in drafter._jobs(lane_root) if r.get("id") == ident]
    if any(r.get("stage") == "DRAFTING" for r in rows):
        return False
    spent = sum(1 for r in rows if not drafter._interrupted(r))
    limit = 1 if directed else drafter.MAX_ATTEMPTS
    return spent >= limit and drafter._draft_state(lane_root, ident) != "UNJUDGED"


def settle(
    lane_root: Path, items: list[dict[str, Any]], state: dict[str, Any]
) -> list[dict[str, Any]]:
    """Advance each ACTIVE plan: DONE when every subtask is merged, BLOCKED when one has exhausted its attempts."""
    changes = []
    for plan in load_plans(lane_root):
        if plan.get("status") != "ACTIVE":
            continue
        rows = {
            s["key"]: refill._slice_status(f"{plan['parent_id']}-{s['key']}", items, state)
            for s in plan["subtasks"]
        }
        kinds = {s["key"]: s.get("kind") == "edit" for s in plan["subtasks"]}
        statuses = {
            key: (
                "EXHAUSTED"
                if status in {"NEW", "FAILED"} and drafting_exhausted(lane_root, ident, directed=kinds[key])
                else status
            )
            for key, (status, ident) in rows.items()
        }
        if all(v == "DONE" for v in statuses.values()) and plan.get("residual"):
            plan.update(
                status="PARTIAL",
                blocker="PARTIAL_PLAN_DELIVERED: every local step merged; the residual returns to the stronger builder: "
                + "; ".join(str(r)[:120] for r in plan["residual"][:3]),
                completed_at=_now(),
            )
        elif all(v == "DONE" for v in statuses.values()):
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


def _read_at(clone: Path, base: str) -> Callable[[str], str | None]:
    def read(path: str) -> str | None:
        done = subprocess.run(
            ["git", "-C", str(clone), "show", f"{base}:{path}"],
            capture_output=True, text=True, check=False, timeout=60,
        )
        return done.stdout.replace("\r\n", "\n") if done.returncode == 0 else None

    return read


def _python_files_at(clone: Path, base: str) -> list[str]:
    done = subprocess.run(
        ["git", "-C", str(clone), "ls-tree", "-r", "--name-only", base],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return [line for line in done.stdout.splitlines() if line.endswith(".py")] if done.returncode == 0 else []


def local_target_missing(request: dict[str, Any], clone: Path, base: str) -> str | None:
    """None when a local subtask could exist for this slice; otherwise the exact structural reason.

    The local builder can only create a new Python module or edit an existing one, so a slice whose allowed paths hold
    neither (documentation or tests only, or no Python at all) cannot be decomposed however the model answers. That is
    decided here, from the repository at the exact base, before any model time is spent.
    """
    code = [p for p in _python_files_at(clone, base) if not TEST_FILE.search(p)]
    for candidate in module_candidates(request, "planning_probe", set(), _exists_at(clone, base)):
        # a new module needs a directory that already holds Python (a C++ Source/ does not); any placement will do
        folder = candidate.rsplit("/", 1)[0] + "/" if "/" in candidate else ""
        if any(path.startswith(folder) for path in code):
            return None
    allowed, forbidden = list(request["allowed_paths"]), list(request.get("forbidden_paths") or [])
    for path in code:
        if _under(path, allowed) and not _under(path, forbidden):
            return None
    return (
        "NO_LOCAL_TARGET_IN_SCOPE: the slice's allowed paths hold no place for a new Python module and no existing "
        "Python module to edit"
    )


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
        if not result.get("ok") and is_memory_wait(result.get("reason", "")):
            # No memory, no free model slot, or the memory brake cancelled the run: a wait, not a failed attempt.
            _job_path(lane_root, job["id"], job["attempt"]).unlink(missing_ok=True)
            if release is not None and job.get("lease_id"):
                try:
                    release(job["lease_id"])
                except Exception:  # a lease that is already gone changes nothing
                    pass
            outcomes.append({"id": job["id"], "status": "WAITING_FOR_MEMORY", "reason": str(result.get("reason"))[:300]})
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
        # Recomputed here against the authoritative clone at the exact base, never taken from the worker: the same
        # deterministic salvage the proposer applied (review P1 on #659: the strict validator here rejected every
        # partial plan the proposer had accepted, so none ever reached write_plan through the real lifecycle).
        plan, problems = salvage_plan(
            result.get("raw"),
            request,
            exists=_exists_at(Path(job["clone"]), job["base"]),
            read=_read_at(Path(job["clone"]), job["base"]),
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
        terminal = next(
            (r.get("reason") for r in mine if any(m in str(r.get("reason")) for m in TERMINAL_MARKERS)), None
        )
        if terminal:  # an impossible task is recorded and released, never retried
            block(lane_root, request, f"DECOMPOSITION_NOT_POSSIBLE: {terminal}")
            outcomes.append({"id": request["id"], "status": "BLOCKED", "reason": str(terminal)[:300]})
            continue
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
        missing = local_target_missing(request, clone, base)
        if missing:  # decided from the repository itself: no model call, no attempt, the stronger builder is released
            block(lane_root, request, missing)
            outcomes.append({"id": request["id"], "status": "BLOCKED", "reason": missing})
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
    return next(module_candidates(request, name, taken, exists), None)


def module_candidates(
    request: dict[str, Any], name: str, taken: set[str], exists: Callable[[str], bool]
) -> Iterator[str]:
    """Every free path for ``name`` the slice's allowed code paths permit, in pattern order."""
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
            yield candidate


def outline_prompt(request: dict[str, Any], brief: str, feedback: str = "") -> str:
    criteria = "\n".join(
        f"{i}. {c}" for i, c in enumerate(request["slice_acceptance"], start=1) if not is_gate_line(str(c))
    )
    return (
        f"Split this software slice into at most {MAX_SUBTASKS} small steps that together deliver every numbered "
        f"criterion.\nSLICE: {request['objective'][:900]}\nCRITERIA:\n{criteria}\n{brief[:500]}\n"
        'Each step is kind "new" (a new module with ONE pure function, no I/O, no globals) or kind "edit" (change at '
        'most 3 named units, functions or classes, of ONE existing module listed above; give "module" and "targets"). '
        "If part of the slice is neither (a program run, a UI, the network, many files), list it in residual instead of "
        "inventing a step for it.\n"
        'Answer JSON only: {"functions":[{"name":"snake_case_label","kind":"new","purpose":"one sentence",'
        '"criteria":[1],"depends_on":[]},{"name":"other_label","kind":"edit","module":"pkg/existing.py",'
        '"targets":["existing_function"],"purpose":"required behaviour","criteria":[2],"depends_on":[]}],"residual":[]}'
        + (f"\nYour previous answer was rejected: {feedback[:900]}" if feedback else "")
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


def normalize_signature(signature: Any) -> str:
    """Strip the model's whitespace and add the trailing colon it commonly omits (the only repair made to a def line)."""
    text = " ".join(str(signature or "").split())
    return text if not text or text.endswith(":") else text + ":"


def import_hint(module_path: str) -> str:
    """The dotted module a test would import (a leading src/ is a layout directory, not a package)."""
    parts = module_path[:-3].split("/")
    return ".".join(parts[1:] if parts[0] == "src" and len(parts) > 1 else parts)


def edit_prompt(
    request: dict[str, Any], entry: dict[str, Any], source: str, feedback: str = ""
) -> str:
    units = directed_edit.units(source)
    shown = "\n\n".join(directed_edit.unit_source(source, units[t]) for t in entry["targets"] if t in units)
    rest = directed_edit.outline(source, set(entry["targets"]))
    return (
        f"Slice context: {request['objective'][:300]}\nRequired behaviour change in {entry['module']} "
        f"(units {', '.join(entry['targets'])}): {entry['purpose']}\n"
        f"CURRENT CODE OF THE UNITS:\n{shown[:2200]}\nREST OF THE MODULE (signatures only):\n{rest[:900]}\n"
        f"Write a pytest test file that FAILS on the current code and passes once the behaviour is implemented. Import "
        f"with `from {import_hint(entry['module'])} import ...`. Use plain asserts on exact values, no mocks of the "
        "units under test, no file or network access. Give a done_when sentence stating the behaviour.\n"
        'Answer JSON only: {"test_source":"import ...\\n\\ndef test_x():\\n    assert ...","done_when":"..."}'
        + (f"\nYour previous answer was rejected: {feedback[:300]}" if feedback else "")
    )


def test_for(request: dict[str, Any], stem: str, taken: set[str], exists: Callable[[str], bool]) -> str | None:
    """A new test path under the slice's allowed test paths (assembled by code, never chosen by the model)."""
    for pattern in request["allowed_paths"]:
        pattern = pattern.replace("\\", "/")
        if "test" not in pattern.lower() or pattern.endswith((".md", ".json")):
            continue
        if pattern.endswith("/**") or pattern.endswith("/*"):
            candidate = pattern.rsplit("/", 1)[0] + f"/test_{stem}.py"
        elif "*" in pattern:
            candidate = pattern.replace("**", stem).replace("*", stem, 1)
        else:
            continue
        base_name = candidate.rsplit("/", 1)[-1]
        if not base_name.startswith("test_"):
            candidate = candidate.rsplit("/", 1)[0] + "/test_" + base_name
        if (
            _normal_path(candidate)
            and _under(candidate, request["allowed_paths"])
            and candidate not in taken
            and not _under(candidate, list(request.get("forbidden_paths") or []))
            and not exists(candidate)
        ):
            return candidate
    return None


def _outline_entries(outline: Any) -> list[dict[str, Any]] | None:
    entries = outline.get("functions") if isinstance(outline, dict) else None
    if isinstance(entries, list) and entries and all(isinstance(e, dict) for e in entries):
        return entries
    return None


NEAR_MISS_CUTOFF = 0.8  # a named unit this close to exactly one real unit is a typo, not a different function


def ground_edit_targets(
    entries: list[dict[str, Any]], units_of: Callable[[str], set[str] | None]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Correct edit targets the model misspelled and report the ones that do not exist.

    The 7B model often names a plausible unit that is not in the module (measured: ``align_notes`` for a module that has
    other names). A target within ``NEAR_MISS_CUTOFF`` of exactly one real unit is replaced by it; anything else is
    reported with the module's real unit names so the model can be asked once more with the truth in front of it.
    Entries that are not edits, or whose module cannot be read, are left for the existing validation to judge.
    """
    import difflib

    grounded: list[dict[str, Any]] = []
    problems: list[str] = []
    for entry in entries:
        module, targets = str(entry.get("module", "")), entry.get("targets")
        real = units_of(module) if entry.get("kind") == "edit" and module else None
        if not real or not isinstance(targets, list):
            grounded.append(entry)
            continue
        fixed, missing, snapped = [], [], []
        purpose_words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(entry.get("purpose", ""))))
        for target in (str(t) for t in targets):
            near = [] if target in real else difflib.get_close_matches(target, sorted(real), n=2, cutoff=NEAR_MISS_CUTOFF)
            # a close name is only a typo when the model's own purpose does not point at a different real unit
            elsewhere = sorted((purpose_words & real) - set(near))
            if target in real:
                fixed.append(target)
            elif len(near) == 1 and not elsewhere:
                fixed.append(near[0])
                snapped.append({"module": module, "from": target, "to": near[0],
                                "similarity": round(difflib.SequenceMatcher(None, target, near[0]).ratio(), 3)})
            else:
                fixed.append(target)
                missing.append(target)
        grounded.append({**entry, "targets": list(dict.fromkeys(fixed)), **({"snapped": snapped} if snapped else {})})
        if missing:
            problems.append(f"units {missing} do not exist in {module}; the real units are {sorted(real)[:25]}")
    return grounded, problems


def _staged_new(request, entry, index, path, ask, trail):
    name = str(entry["name"])
    purpose = str(entry.get("purpose", name))[:200]
    detail: Any = {}
    feedback = ""
    for _try in range(MAX_FUNCTION_TRIES):
        detail = ask(function_prompt(request, {"name": name, "purpose": purpose}, feedback))
        if not isinstance(detail, dict):
            bad = ["answer is not a JSON object"]
        else:
            detail["signature"] = normalize_signature(detail.get("signature"))
            detail["acceptance_examples"] = normalize_examples(name, detail.get("acceptance_examples"))
            probe = {
                "key": f"t{index}",
                "signature": detail["signature"],
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
    return {
        "key": f"t{index}",
        "title": purpose[:120],
        "module_path": path,
        "function_name": name,
        "signature": str(detail.get("signature", "")),
        "done_when": str(detail.get("done_when") or purpose),
        "acceptance_examples": detail.get("acceptance_examples") or [],
    }


def _staged_edit(request, entry, index, taken_tests, ask, exists, read, trail):
    module = str(entry.get("module", ""))
    targets = entry.get("targets") if isinstance(entry.get("targets"), list) else []
    purpose = str(entry.get("purpose", entry["name"]))[:300]
    sub: dict[str, Any] = {"key": f"t{index}", "kind": "edit", "title": purpose[:120], "module_path": module,
                           "targets": [str(t) for t in targets], "done_when": purpose, "test_source": "", "test_path": ""}
    source = read(module) if read is not None and _normal_path(module) and exists(module) else None
    if source is None or not targets:
        sub["unresolved"] = f"{module or '(no module)'} does not exist at the base or no units were named"
        return sub  # validation reports exactly what is missing
    try:
        found = directed_edit.units(source)
    except SyntaxError:
        return sub
    if any(t not in found for t in sub["targets"]):
        sub["unresolved"] = f"units {[t for t in sub['targets'] if t not in found]} do not exist in {module}"
        return sub
    stem = f"{module.rsplit('/', 1)[-1][:-3]}_{sub['targets'][0].lstrip('_').lower()}"
    path = test_for(request, stem, taken_tests, exists)
    if path is None:
        sub["unresolved"] = "no free allowed path for the test file"
        return sub
    taken_tests.add(path)
    sub["test_path"] = path
    feedback = ""
    for _try in range(MAX_FUNCTION_TRIES):
        detail = ask(edit_prompt(request, {"name": entry["name"], "module": module, "targets": sub["targets"],
                                           "purpose": purpose}, source, feedback))
        detail = detail if isinstance(detail, dict) else {}
        sub["test_source"] = str(detail.get("test_source", ""))
        sub["done_when"] = str(detail.get("done_when") or purpose)
        bad: list[str] = []
        declared = {"module_path": module, "test_path": path, "test_source": sub["test_source"],
                    "targets": sub["targets"]}
        if drafter.directed_spec({"directed_edit": declared}) is None:
            bad.append("the test must parse, define test_*, assert, and exercise a named unit")
        trail.append({"edit": module, "problems": bad})
        if not bad:
            break
        feedback = "; ".join(bad)
    return sub


def propose_staged(
    request: dict[str, Any],
    brief: str,
    ask: Callable[[str], Any],
    *,
    exists: Callable[[str], bool],
    read: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Outline, then one small call per step; assemble; validate. Same result shape as ``propose``."""
    trail: list[dict[str, Any]] = []
    try:
        outline = ask(outline_prompt(request, brief))
        entries = _outline_entries(outline)
        if entries is None:
            outline = ask(outline_prompt(request, brief, 'answer {"functions":[...],"residual":[...]} with a step'))
            entries = _outline_entries(outline)
        if entries is None:
            return {"ok": False, "reason": "PLAN_REJECTED: the outline lists no functions", "trail": trail}
        entries = entries[:MAX_SUBTASKS]

        def units_of(module: str) -> set[str] | None:
            if read is None or not _normal_path(module) or not exists(module):
                return None
            try:
                return set(directed_edit.units(read(module) or ""))
            except SyntaxError:
                return None

        entries, ungrounded = ground_edit_targets(entries, units_of)
        if ungrounded:  # one more outline with the module's real unit names, never a guess by code
            again = ask(outline_prompt(request, brief, "; ".join(ungrounded)[:900]))
            second = _outline_entries(again)
            if second is not None:
                second, still = ground_edit_targets(second[:MAX_SUBTASKS], units_of)
                trail.append({"grounding_retry": True, "first": ungrounded, "remaining": still})
                outline, entries = again, second
        snaps = [s for e in entries for s in e.get("snapped", [])]
        if snaps:  # every correction the code made is visible in the plan trail: original name -> real unit
            trail.append({"snapped_targets": snaps})
        names = [str(e.get("name", "")) for e in entries]
        if len(set(names)) != len(names) or not all(NAME_OK.match(n) for n in names):
            return {"ok": False, "reason": "PLAN_REJECTED: function names must be unique snake_case", "trail": trail}
        residual = outline.get("residual")
        if not isinstance(residual, list):
            return {
                "ok": False,
                "reason": "PLAN_REJECTED: residual must be a list (empty only when nothing is left to do)",
                "trail": trail,
            }
        subtasks: list[dict[str, Any]] = []
        taken: set[str] = set()
        taken_tests: set[str] = set()
        for index, entry in enumerate(entries, start=1):
            if entry.get("kind") == "edit":
                made = _staged_edit(request, entry, index, taken_tests, ask, exists, read, trail)
            else:
                path = module_for(request, str(entry["name"]), taken, exists)
                if path is None:
                    free = module_for(request, "planning_probe", set(), exists) is not None
                    reason = (
                        f"PLAN_REJECTED: a module named {entry['name']} already exists in scope; choose another function name"
                        if free  # the scope has room, only this name is taken: an ordinary slip, retried
                        else f"PLAN_REJECTED: NO_LOCAL_TARGET_IN_SCOPE: no free allowed path for {entry['name']}"
                    )
                    return {"ok": False, "reason": reason, "trail": trail}
                taken.add(path)
                made = _staged_new(request, entry, index, path, ask, trail)
            earlier = names[: index - 1]
            wanted = entry.get("depends_on") if isinstance(entry.get("depends_on"), list) else []
            made["depends_on"] = [f"t{names.index(d) + 1}" for d in wanted if d in earlier][:MAX_DEPENDENCIES]
            subtasks.append(made)
        coverage: dict[str, list[str]] = {}
        for index, entry in enumerate(entries, start=1):
            numbers = entry.get("criteria") if isinstance(entry.get("criteria"), list) else []
            for number in numbers:
                coverage.setdefault(str(number), []).append(f"t{index}")
        raw = {"subtasks": subtasks, "coverage": coverage, "residual": residual}
    except Exception as exc:  # an unreachable model is a blocker to record, not a reason to crash the lane
        return {"ok": False, "reason": f"MODEL_UNAVAILABLE: {type(exc).__name__}: {str(exc)[:160]}", "trail": trail}
    plan, problems = salvage_plan(raw, request, exists=exists, read=read)
    trail.append({"assembled": True, "problems": problems,
                  "dropped": (plan or {}).get("dropped", []), "residual": (plan or {}).get("residual", [])})
    if plan is None:
        return {"ok": False, "reason": "PLAN_REJECTED: " + problems[0][:240], "trail": trail, "raw": raw}
    return {"ok": True, "plan": plan, "raw": raw, "trail": trail}
