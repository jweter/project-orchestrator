"""Single bounded Iron God worker for one self-planned product turn.

The product lane launches this process for exactly one portfolio repository
turn in an isolated worktree of the lane's own clone, based on the freshly
fetched ``origin/main``. The turn has two short steps sized for the local
model (measured on Azathoth: a whole self-directed aider session against a
large repository rewrote existing files from imagination and applied nothing):

1. Plan: one direct model call with a bounded brief (the repository's agent
   contract and README openings, its package layout, the shared working
   memory) returns a JSON slice: one NEW module, one NEW test file, an
   objective and acceptance criteria. The plan is validated before any code.
2. Build: the proven commissioning setup -- aider with exactly those two files,
   no repository map, whole-file edits.

The worker then enforces the scope envelope and the builder's change gates,
commits, and runs the repository's canonical preflight at the exact head
under the supervised runtime, re-checking the owner's execution authority
throughout. It never pushes, merges or publishes.
"""

from __future__ import annotations

import argparse
import ast
import http.client
import json
import keyword
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT / "src"), str(ROOT)):
    if _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

from project_orchestrator.execution_authority import evaluate_execution_authority  # noqa: E402
from project_orchestrator.execution_runtime import NativeWindowsRuntime, RuntimeRequest  # noqa: E402
from project_orchestrator.iron_god_calibration import remove_tree  # noqa: E402
from scripts.iron_god_canon_worker import (  # noqa: E402
    _git,
    _now,
    _tail,
    _under,
    _write,
    changed_paths,
    lean_aider_invocation,
    private_temp_env,
    worker_message,
)

PREFLIGHT_COMMAND = ("engineering/preflight.py",)
# The hidden worker console is narrower than a CI runner's, and Rich wraps CLI errors to the
# console width: on 2026-10-29 two knowledge-engine-core tests that match a wrapped message failed
# only under the worker (KEC-9). The preflight sees the width a non-terminal CI job gets.
PREFLIGHT_CONSOLE = {"COLUMNS": "80", "LINES": "24"}
NOTE_CHARS = 600
BRIEF_CHARS = 2500
MAX_TEXT = 1200  # Canon's bounded handoff text
PLAN_ATTEMPTS = 2


class PlanRejected(ValueError):
    """The model's slice plan is unusable; nothing was built."""


def repository_brief(worktree: Path, base: str, roots: list[str]) -> str:
    """Bounded planning context: agent contract + README openings and the package layout."""
    parts = []
    for name in ("AGENTS.md", "README.md"):
        path = worktree / name
        if path.is_file():
            parts.append(f"--- {name} (opening) ---\n" + path.read_text(encoding="utf-8", errors="ignore")[:BRIEF_CHARS])
    listed = _git(worktree, "ls-tree", "-r", "--name-only", base).stdout.splitlines()
    packages = python_packages(listed, roots)
    modules = sorted({p for p in listed if p.endswith(".py") and p.rsplit("/", 1)[0] + "/" in packages
                      and not p.endswith("__init__.py")})
    parts.append("--- Python packages (put the new module in one of these) ---\n" + "\n".join(packages[:30]))
    parts.append("--- existing modules (do NOT reuse these file names) ---\n" + "\n".join(modules[:60]))
    return "\n\n".join(parts)


def python_packages(listed: list[str], roots: list[str]) -> list[str]:
    """Real packages (directories with ``__init__.py``) inside the envelope, excluding tests.

    A repository with no real package at all (Project-Everward: loose tooling scripts) falls back to its script
    directories - directories holding Python modules, imported as namespace packages from the repository root.
    """
    def usable(path: str) -> bool:
        return _under(path, roots) and not any(part in {"tests", "test"} for part in path.split("/"))

    real = {p.rsplit("/", 1)[0] + "/" for p in listed if p.endswith("/__init__.py") and usable(p)}
    if real:
        return sorted(real)
    return sorted({p.rsplit("/", 1)[0] + "/" for p in listed if p.endswith(".py") and "/" in p and usable(p)})


def planning_prompt(packet: dict[str, Any], brief: str) -> str:
    memory = (packet.get("working_memory") or "").strip()[:1500]
    attempted = sorted({name for name in (packet.get("attempted_functions") or [])
                        if isinstance(name, str) and name.isidentifier()})
    excluded = ", ".join(attempted[:50]) or "(none)"
    return (
        f"You plan ONE small slice of work for the repository {packet['repository']}.\n"
        f"Previously attempted function names (never propose these): {excluded}. "
        f"The validator rejects all {len(attempted)} attempted names, including any not shown.\n"
        "Choose ONE new pure Python function that is useful for this repository's purpose. It takes and returns "
        "plain values (str, int, float, bool, list, dict, tuple, None): no files, audio, images, network, time or "
        "randomness, and it uses only the Python standard library. It goes in ONE new module inside one of the "
        "existing packages listed below. Prefer functions whose results are easy to state exactly, such as text "
        "cleanup, filtering, counting, grouping or validation by explicit rules; avoid percentages and fuzzy "
        "scores.\n\n"
        f"{brief}\n\n{memory}\n\n"
        'Reply with JSON only: {"function_name": "<snake_case name>", '
        '"signature": "<def line, e.g. def clamp(value: int, low: int, high: int) -> int:>", '
        '"module_path": "<new module path inside an existing package>", '
        '"objective": "<the exact rule the function applies, precise enough that two people would write the '
        'same code; not just the topic>", '
        '"examples": [{"call": "<a Python call of the function with literal arguments>", '
        '"why": "<the short arithmetic or rule that gives the result>", '
        '"expected": <the exact JSON value it must return>}, ...3 to 5 examples covering normal and edge cases], '
        "Every argument an example passes must be a parameter of the signature, and every expected value must "
        "follow from its 'why'. "
        '"note": "<one sentence: why this helps the repository and what could come next>"}'
    )


def ollama_json(prompt: str, *, model: str | None = None, timeout: int = 1800) -> dict[str, Any]:
    from scripts.autonomous_slice_builder import MODEL

    base = os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434").rstrip("/")
    name = (model or MODEL).split("/", 1)[-1]
    body = {"model": name, "prompt": prompt, "stream": False, "format": "json",
            "options": {"temperature": 0.2, "num_ctx": 8192, "num_predict": 1200}}
    if name != MODEL.split("/", 1)[-1]:
        body["keep_alive"] = 0  # a larger planner must leave RAM before the builder model loads (16 GB host)
    request = urllib.request.Request(base + "/api/generate", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        reply = json.load(response)
    return json.loads(reply.get("response") or "{}")


def release_builder_model(timeout: float = 15.0) -> bool:
    """Unload the resident builder model before a heavy gate; True when Ollama confirmed it.

    The model stays loaded for minutes after its last reply, and on the 16 GB host it overlapped mypy
    and the full test run (2026-10-31: 404 MB free). The shared model lock is taken without waiting, so
    a generation in progress is never interrupted; any failure only leaves the model resident, as before.
    """
    import shutil

    from scripts.autonomous_slice_builder import MODEL, _ollama_lock_dir

    lock = _ollama_lock_dir()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.mkdir()
    except OSError:
        return False  # another process holds the model slot
    try:
        (lock / "owner").write_text(f"{os.getpid()}\n{time.time()}\n", encoding="utf-8")
        base = os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434").rstrip("/")
        body = {"model": MODEL.split("/", 1)[-1], "keep_alive": 0}
        request = urllib.request.Request(base + "/api/generate", data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
        return True
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def import_path(module: str, worktree: Path | None = None) -> str:
    """Dotted import path for a module file, honouring a src/ layout and an app-dir import root.

    A repository whose top directory is not a package (no ``__init__.py``) while ``tests/<top>/__init__.py`` exists
    runs its code with that directory on ``sys.path`` (VoiceStudio: ``--app-dir backend``, ``from services.x import``);
    ``from backend.services.x`` would resolve to the tests package instead (VoiceStudio S2-r1, 2026-11-02).
    """
    parts = module[:-3].split("/")
    if worktree is not None and len(parts) > 2 and parts[0] != "src" and not (worktree / parts[0] / "__init__.py").is_file()             and (worktree / "tests" / parts[0] / "__init__.py").is_file():
        return ".".join(parts[1:])
    return ".".join(parts[1:] if parts[0] == "src" else parts)


def declared_test(packet: dict[str, Any], module: str) -> str | None:
    """The one test file the owner's roadmap (or the approved slice) names for this module, if any."""
    wanted = f"test_{module.rsplit('/', 1)[-1]}"
    declared = [*(packet.get("declared_tests") or []), *((packet.get("slice") or {}).get("tests") or [])]
    found = sorted({str(t).replace("\\", "/") for t in declared
                    if str(t).startswith("tests/") and str(t).rsplit("/", 1)[-1] == wanted})
    return found[0] if len(found) == 1 else None


def validate_plan(plan: Any, worktree: Path, base: str, packet: dict[str, Any]) -> dict[str, Any]:
    """Fail-closed checks before any code: one NEW module in an existing package, exact literal examples."""
    if not isinstance(plan, dict):
        raise PlanRejected("PLAN_NOT_AN_OBJECT")
    name = str(plan.get("function_name") or "")
    if not name.isidentifier() or keyword.iskeyword(name) or name.startswith("_"):
        raise PlanRejected(f"PLAN_FUNCTION_NAME_INVALID: {name[:60]}")
    if name in (packet.get("attempted_functions") or []):  # measured: the model repeats ideas it was told to avoid
        raise PlanRejected(f"PLAN_ALREADY_ATTEMPTED: {name} was already tried in this repository; choose a "
                           "different function")
    module = str(plan.get("module_path") or "").replace("\\", "/")
    if not module.endswith(".py") or module.startswith("/") or ".." in module.split("/") or "/" not in module:
        raise PlanRejected(f"PLAN_PATH_INVALID: {module[:120]}")
    if not _under(module, packet["paths"]) or module.startswith("tests/"):
        raise PlanRejected("PLAN_PATH_OUTSIDE_ENVELOPE")
    test = declared_test(packet, module) or f"tests/test_{module.rsplit('/', 1)[-1]}"

    def exists(path: str) -> bool:
        return _git(worktree, "cat-file", "-e", f"{base}:{path}").returncode == 0

    if exists(module):  # the function is new: give it its own module named after it
        module = f"{module.rsplit('/', 1)[0]}/{name}.py"
        test = declared_test(packet, module) or f"tests/test_{name}.py"
    forbidden = [*packet["planning"]["forbidden_paths"], *(packet.get("avoid_paths") or [])]
    if _under(module, forbidden) or _under(test, forbidden):
        raise PlanRejected("PLAN_PATH_FORBIDDEN_OR_UNDER_REVIEW")
    for path in (module, test):
        if exists(path):
            raise PlanRejected(f"PLAN_WOULD_EDIT_EXISTING_FILE: {path}")
    package = module.rsplit("/", 1)[0]
    if _git(worktree, "cat-file", "-e", f"{base}:{package}/__init__.py").returncode != 0:
        listed = _git(worktree, "ls-tree", "-r", "--name-only", base).stdout.splitlines()
        if f"{package}/" not in python_packages(listed, packet["paths"]):  # not even a script directory
            raise PlanRejected(f"PLAN_PACKAGE_MISSING: {package}")
    signature = " ".join(str(plan.get("signature") or "").split())
    if not signature.startswith(f"def {name}(") or len(signature) > 300:
        raise PlanRejected("PLAN_SIGNATURE_INVALID")
    try:
        declared = ast.parse(signature.rstrip(":") + ": ...").body[0].args
    except (SyntaxError, IndexError, AttributeError):
        raise PlanRejected("PLAN_SIGNATURE_INVALID") from None
    params = [a.arg for a in [*declared.posonlyargs, *declared.args, *declared.kwonlyargs]]
    required = len(declared.posonlyargs) + len(declared.args) - len(declared.defaults)
    objective = " ".join(str(plan.get("objective") or "").split())
    if not 20 <= len(objective) <= 600:
        raise PlanRejected("PLAN_SPEC_INCOMPLETE")
    examples = []
    for item in plan.get("examples") or []:
        try:
            call = " ".join(str(item["call"]).split())
            tree = ast.parse(call, mode="eval").body
            if not (isinstance(tree, ast.Call) and isinstance(tree.func, ast.Name) and tree.func.id == name):
                raise ValueError("not a call of the planned function")
            for arg in [*tree.args, *(k.value for k in tree.keywords)]:
                ast.literal_eval(arg)
            expected = repr(item["expected"])
            ast.literal_eval(expected)
            if not str(item.get("why") or "").strip():
                raise ValueError("an example must show why it has its expected value")
            # bytes are detected in the parsed call, not by text (measured: the key 'b' in "('b', 1)" was
            # mistaken for a bytes literal and the example silently dropped)
            if any(isinstance(node, ast.Constant) and isinstance(node.value, bytes) for node in ast.walk(tree)) or (
                    isinstance(item["expected"], str) and item["expected"][:2] in {"b'", 'b"'}):
                raise ValueError("bytes cannot be expressed as JSON plan values")
        except (KeyError, TypeError, ValueError, SyntaxError):
            continue
        keywords = [k.arg for k in tree.keywords]
        positional = len(tree.args)
        bound = set(params[:positional]) | set(keywords)
        if (any(k not in params for k in keywords) or positional > len(declared.posonlyargs) + len(declared.args)
                or not set(params[:required]) <= bound):
            raise PlanRejected(f"PLAN_EXAMPLE_DOES_NOT_MATCH_SIGNATURE: {call[:120]}")
        examples.append({"call": call, "expected": expected})
    if not 3 <= len(examples) <= 8:
        raise PlanRejected(f"PLAN_EXAMPLES_INSUFFICIENT: {len(examples)} usable")
    criteria = [f"{e['call']} returns {e['expected']}" for e in examples]
    criteria.append("the module imports only the Python standard library")
    task = (f"Create {module} defining exactly this function: {signature} -- {objective} "
            "Keep it a pure function with a docstring.")
    if any(len(text) > MAX_TEXT for text in [task, *criteria]):
        raise PlanRejected("PLAN_TEXT_UNBOUNDED")
    return {"function_name": name, "signature": signature, "objective": objective, "task": task,
            "module_path": module, "test_path": test,
            "import_path": import_path(module, worktree),
            "examples": examples, "acceptance_criteria": criteria,
            "note": " ".join(str(plan.get("note") or "").split())[:NOTE_CHARS]}


def render_tests(plan: dict[str, Any]) -> str:
    """The slice's regression tests, written by the worker from the plan's exact examples (never by the model)."""
    lines = [f"from {plan['import_path']} import {plan['function_name']}", ""]
    for index, example in enumerate(plan["examples"], 1):
        # singletons compare by identity: repository linters reject `== True` (E712) and `== None` (E711)
        op = "is" if example["expected"] in {"True", "False", "None"} else "=="
        lines += ["", f"def test_{plan['function_name']}_example_{index}() -> None:",
                  f"    assert {example['call']} {op} {example['expected']}", ""]
    return "\n".join(lines).rstrip() + "\n"


CODING_GUIDE = (
    "Coding guide: write the complete module; precise type hints (list[str], dict[str, int], never bare list or "
    "dict) and a short docstring on the function; handle empty "
    "input explicitly; return values, never print; no files, network, time or randomness; standard library only; "
    "keep it under 60 lines; follow the style of the example module from this package, if one is shown."
)


def style_example(worktree: Path, module: str, limit: int = 1500) -> str:
    """The smallest real module in the target package, as text only (its path is never named)."""
    package = worktree / module.rsplit("/", 1)[0]
    candidates = []
    for path in package.glob("*.py"):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if 8 <= text.count("\n") and len(text) <= limit and "def " in text:
            candidates.append((len(text), text))
    return min(candidates)[1] if candidates else ""


def build_message(packet: dict[str, Any], plan: dict[str, Any], worktree: Path | None = None) -> str:
    """The proven commissioning prompt naming only the planned module, plus the lean coding guide."""
    message = worker_message({"task": plan["task"], "paths": [plan["module_path"]],
                              "planning": {"acceptance_criteria": plan["acceptance_criteria"]}})
    example = style_example(worktree, plan["module_path"]) if worktree is not None else ""
    message += "\n\n" + CODING_GUIDE
    if example:
        message += "\n\nStyle example (an existing module in this package; do not edit it):\n```\n" + example + "\n```"
    return message


def repair_message(plan: dict[str, Any], failure: str) -> str:
    """The failing assertions, bounded, for one repair of the module only (the tests are the spec)."""
    lines = [line for line in failure.splitlines() if line.startswith(("E ", ">", "FAILED"))][:30]
    return (f"The function in {plan['module_path']} does not yet meet its specification. Its tests in "
            f"{plan['test_path']} are correct and must not change. Fix only {plan['module_path']} so that "
            "the repository's type checker and linter pass and every listed example holds:\n" + "\n".join(f"- {c}" for c in plan["acceptance_criteria"])
            + "\n\nFailing assertions:\n" + "\n".join(lines))


def stdlib_only_failure(worktree: Path, module: str) -> str | None:
    """The new module may import only the standard library and its own top-level package."""
    try:
        tree = ast.parse((worktree / module).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return "MODULE_UNPARSEABLE"
    own = import_path(module, worktree).split(".")[0]
    for node in ast.walk(tree):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
            [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
        for name in names:
            top = name.split(".")[0]
            if top and top != own and top not in sys.stdlib_module_names:
                return f"NON_STDLIB_IMPORT: {name}"
    return None


def production_planner(worktree: Path, packet: dict[str, Any], check: Callable[[], None],
                       feedback: str = "") -> dict[str, Any]:
    check()
    prompt = planning_prompt(packet, repository_brief(worktree, packet["base_sha"], packet["paths"]))
    raw = ollama_json(prompt + ("\n\n" + feedback if feedback else ""), model=packet.get("planner_model"))
    check()
    return raw


def production_agent(worktree: Path, message: str, files: list[str], check: Callable[[], None]) -> None:
    from scripts.autonomous_slice_builder import MAX_AIDER_SECONDS, _acquire_ollama_lock, aider_invocation
    import shutil

    argv, env = lean_aider_invocation(aider_invocation, message, files)
    lock = _acquire_ollama_lock()
    try:
        result = NativeWindowsRuntime().execute_supervised(
            RuntimeRequest(tuple(argv), worktree, env, MAX_AIDER_SECONDS), check, interval=2.0,
        )
    finally:
        shutil.rmtree(lock, ignore_errors=True)
    if result.returncode != 0:
        raise RuntimeError(f"AIDER_EXIT_{result.returncode}: {_tail(result.stderr)[-300:]}")


def transcript_tail(worktree: Path, limit: int = 6000) -> str:
    """Bounded end of aider's chat history: diagnosis evidence for a failed turn, never authority."""
    try:
        text = (worktree / ".aider.chat.history.md").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    return text[-limit:]


def quick_test(worktree: Path, test: str, timeout: int = 300) -> dict[str, Any]:
    """Fail fast: the slice's own tests (seconds) before the repository's full preflight (minutes)."""
    import subprocess

    temp = worktree.parent / f"{worktree.name}.quick.tmp"
    env = private_temp_env({**os.environ, "PYTHONPATH": os.pathsep.join([str(worktree / "src"), str(worktree)]),
                            "PYTHONDONTWRITEBYTECODE": "1"}, temp)  # leave nothing behind in the worktree
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test], cwd=worktree,
                              env=env, capture_output=True, text=True, check=False, timeout=timeout)
        return {"command": f"python -m pytest -q {test}", "returncode": proc.returncode, "ok": proc.returncode == 0,
                "output_tail": _tail(proc.stdout + "\n" + proc.stderr)}
    except subprocess.TimeoutExpired:
        return {"command": f"python -m pytest -q {test}", "returncode": None, "ok": False, "output_tail": "TIMEOUT"}
    finally:
        remove_tree(temp)


NO_OPINION = object()


def production_rederive(packet: dict[str, Any], plan: dict[str, Any], call: str) -> Any:
    """A fresh, independent derivation of one example: objective, signature and call only (no code, no answer)."""
    prompt = (f"A Python function is specified as:\n{plan['signature']}\n{plan['objective']}\n\n"
              f"What exactly does {call} return? Apply the specification literally, step by step in your head. "
              'Reply with JSON only: {"result": <the exact JSON value it returns>}')
    reply = ollama_json(prompt, model=packet.get("planner_model"))
    return reply["result"] if isinstance(reply, dict) and "result" in reply else NO_OPINION


def actual_result(worktree: Path, plan: dict[str, Any], call: str) -> str | None:
    """repr() of what the built module returns for one example call, or None if it raises."""
    import subprocess

    code = (f"from {plan['import_path']} import {plan['function_name']}\n"
            f"print(repr({call}))")
    roots = [str(worktree / "src"), str(worktree)]
    parts = str(plan.get("module_path") or "").removesuffix(".py").split("/")
    if len(parts) > 2 and parts[0] != "src" and plan["import_path"] == ".".join(parts[1:]):
        roots.insert(0, str(worktree / parts[0]))  # an app-dir import root (pytest gets it from the repo's conftest)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(roots), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run([sys.executable, "-c", code], cwd=worktree, env=env, capture_output=True, text=True,
                              check=False, timeout=30)
    except subprocess.TimeoutExpired:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def adjudicate(worktree: Path, packet: dict[str, Any], plan: dict[str, Any], failure: str,
               check: Callable[[], None], rederive: Callable[..., Any] | None) -> list[dict[str, Any]]:
    """Correct failing examples only where two independent derivations agree against the plan.

    A failing example is corrected to the code's result only when a fresh model
    call, given just the objective, signature and call, derives the same value.
    Every failing example must be resolved this way and at most half of the
    examples may change; otherwise nothing is corrected and the slice fails.
    """
    failing = sorted({int(n) for n in re.findall(rf"test_{plan['function_name']}_example_(\d+)", failure)
                      if "FAILED" in failure})
    examples = plan["examples"]
    if not failing or len(failing) > len(examples) // 2 or any(not 1 <= n <= len(examples) for n in failing):
        return []
    derive = rederive or production_rederive
    corrections = []
    for n in failing:
        check()
        example = examples[n - 1]
        actual = actual_result(worktree, plan, example["call"])
        fresh = derive(packet, plan, example["call"])
        if actual is None or fresh is NO_OPINION or repr(fresh) != actual or actual == example["expected"]:
            return []
        corrections.append({"example": n, "call": example["call"], "was": example["expected"], "now": actual})
    for item in corrections:
        examples[item["example"] - 1]["expected"] = item["now"]
    plan["acceptance_criteria"] = [f"{e['call']} returns {e['expected']}" for e in examples] + [
        "the module imports only the Python standard library"]
    return corrections


def mypy_configured(worktree: Path) -> bool:
    for name, marker in (("pyproject.toml", "[tool.mypy"), ("mypy.ini", "[mypy"), ("setup.cfg", "[mypy")):
        try:
            if marker in (worktree / name).read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def quality_rules(worktree: Path) -> list[str]:
    """The repository's own static gates, stated for the model from its configuration (never relaxed).

    Measured on 2026-10-28 (pilot KEA-6): a behaviourally correct directed edit failed strict mypy
    because its nested parser methods were unannotated; nothing in the prompt said mypy was strict.
    """
    import configparser
    import tomllib

    def toml(name: str) -> dict[str, Any]:
        try:
            return tomllib.loads((worktree / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    tool = toml("pyproject.toml").get("tool", {})
    mypy = tool.get("mypy", {})
    strict = mypy.get("strict") is True or mypy.get("disallow_untyped_defs") is True
    for name in ("mypy.ini", "setup.cfg"):
        parser = configparser.ConfigParser()
        try:
            parser.read(worktree / name, encoding="utf-8")
            strict = strict or any(parser.getboolean("mypy", key, fallback=False)
                                   for key in ("strict", "disallow_untyped_defs"))
        except (configparser.Error, ValueError):
            continue
    rules = []
    if strict:
        rules.append("mypy runs in strict mode: annotate every parameter and the return type of every function "
                     "and method you write, nested ones included (__init__ and methods returning nothing take "
                     "-> None), and give new list or dict attributes a type, e.g. self.parts: list[str] = [].")
    ruff = tool.get("ruff", {})
    for name in ("ruff.toml", ".ruff.toml"):
        ruff = {**ruff, **toml(name)}
    length = ruff.get("line-length")
    if isinstance(length, int) and length > 0:
        rules.append(f"Keep every line at most {length} characters; split a long string into adjacent "
                     "string literals.")
    return rules


def type_check(worktree: Path, files: list[str], check: Callable[[], None]) -> dict[str, Any] | None:
    """The repository's own mypy configuration on the two new files, when it has one (measured: strict mypy
    was the only check a correct slice failed in knowledge-engine-ai)."""
    if not mypy_configured(worktree):
        return None
    environment = repository_environment(worktree, check)
    release_builder_model()  # mypy needs about 1.3 GB beside the 5 GB model
    temp = worktree.parent / f"{worktree.name}.mypy.tmp"
    env = {**os.environ, **environment["overrides"], "MYPY_CACHE_DIR": str(temp),
           "PYTHONPATH": os.pathsep.join([str(worktree / "src"), str(worktree)])}
    try:
        result = NativeWindowsRuntime().execute_supervised(
            RuntimeRequest((environment["python"], "-m", "mypy", *files), worktree, env, 600), check, interval=2.0,
        )
        return {"command": "python -m mypy " + " ".join(files), "returncode": result.returncode,
                "ok": result.returncode == 0, "output_tail": _tail((result.stdout or "") + (result.stderr or ""))}
    finally:
        remove_tree(temp)


def lint_check(worktree: Path, files: list[str], check: Callable[[], None]) -> dict[str, Any] | None:
    """The repository's ruff lint on the new files, after its formatter and safe fixes (measured: E712, E501
    and F401 failed whole preflights that the bounded repair round could have fixed)."""
    try:
        if "[tool.ruff" not in (worktree / "pyproject.toml").read_text(encoding="utf-8"):
            return None
    except OSError:
        return None
    production_format(worktree, files, check)
    environment = repository_environment(worktree, check)
    result = NativeWindowsRuntime().execute_supervised(RuntimeRequest(
        (environment["python"], "-m", "ruff", "check", "--output-format", "concise", *files), worktree,
        {**os.environ, **environment["overrides"]}, 300), check, interval=2.0)
    return {"command": "python -m ruff check " + " ".join(files), "returncode": result.returncode,
            "ok": result.returncode == 0, "output_tail": _tail((result.stdout or "") + (result.stderr or ""))}


def slice_checks(worktree: Path, module: str, test: str, check: Callable[[], None]) -> dict[str, Any]:
    """Fail fast on the slice itself: its generated tests, then the repository's type checker and linter."""
    tests = quick_test(worktree, test)
    if not tests["ok"]:
        return tests
    types = type_check(worktree, [module, test], check)
    if types is not None and not types["ok"]:
        lines = [line for line in types["output_tail"].splitlines() if ": error:" in line][:20]
        return {**types, "output_tail": "E mypy: " + "\nE mypy: ".join(lines) if lines else types["output_tail"]}
    lint = lint_check(worktree, [module, test], check)
    if lint is not None and not lint["ok"]:
        lines = [line for line in lint["output_tail"].splitlines() if ".py:" in line][:20]
        return {**lint, "output_tail": "E ruff: " + "\nE ruff: ".join(lines) if lines else lint["output_tail"]}
    return tests


def production_format(worktree: Path, files: list[str], check: Callable[[], None]) -> str:
    """Apply the repository's own formatter to the new files (measured: a correct slice failed only on format)."""
    try:
        config = (worktree / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return "none"
    tool = "ruff" if "[tool.ruff" in config else "black" if "[tool.black" in config else None
    if tool is None:
        return "none"
    environment = repository_environment(worktree, check)
    env = {**os.environ, **environment["overrides"]}
    if tool == "ruff":
        # safe autofixes only (import order, unused imports); what remains is the preflight's verdict
        NativeWindowsRuntime().execute_supervised(RuntimeRequest(
            (environment["python"], "-m", "ruff", "check", "--fix", "--exit-zero", "-q", *files), worktree, env, 300,
        ), check, interval=2.0)
    argv = (environment["python"], "-m", "ruff", "format", *files) if tool == "ruff" else (
        environment["python"], "-m", "black", "-q", *files)
    result = NativeWindowsRuntime().execute_supervised(RuntimeRequest(argv, worktree, env, 300), check, interval=2.0)
    if result.returncode != 0:
        raise RuntimeError(f"{tool} format failed: {_tail((result.stdout or '') + (result.stderr or ''))[-200:]}")
    return tool


BUILTIN_TYPES = frozenset({"str", "int", "float", "bool", "bytes", "complex", "list", "dict", "set", "frozenset",
                           "tuple", "object", "type"})


def drop_builtin_typing_imports(source: str) -> tuple[str, list[str]]:
    """Remove builtin names the model imported from ``typing`` (``from typing import Dict, str`` cannot import).

    A mechanical repair of one measured model slip (VoiceStudio S2, every attempt). It is only applied when it is
    demonstrably behaviour-preserving, and otherwise the source is returned untouched for the normal gates to judge:

    * none of these names exists in ``typing``, so no import that could succeed is ever altered;
    * an aliased import (``from typing import str as S``) is never repaired - ``S`` would become undefined;
    * the statement must be alone on its lines (no ``;`` neighbours) so nothing else is lost;
    * the repaired module must still parse, which rules out emptying a block (``if X:`` / ``try:`` / ``def``) that held
      the import as its only statement.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source, []
    lines = source.split("\n")
    plan: list[tuple[ast.ImportFrom, list[ast.alias]]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.module == "typing" and node.level == 0):
            continue
        bad = [a for a in node.names if a.name in BUILTIN_TYPES]
        if not bad:
            continue
        if any(a.asname for a in bad):
            return source, []  # a referenced alias would be left undefined: not demonstrably safe
        end_line = node.end_lineno or node.lineno
        before = lines[node.lineno - 1][: node.col_offset]
        after = lines[end_line - 1][(node.end_col_offset or 0):]
        if before.strip() or (after.strip() and not after.strip().startswith("#")):
            return source, []  # shares its line with other code
        plan.append((node, bad))
    if not plan:
        return source, []
    removed: list[str] = []
    for node, bad in sorted(plan, key=lambda item: -item[0].lineno):
        removed += [a.name for a in bad]
        keep = [a for a in node.names if a.name not in BUILTIN_TYPES]
        end_line = node.end_lineno or node.lineno
        indent = lines[node.lineno - 1][: node.col_offset]
        comment = lines[end_line - 1][(node.end_col_offset or 0):].strip()
        replacement = ([indent + "from typing import " + ", ".join(
            a.name + (f" as {a.asname}" if a.asname else "") for a in keep) + (("  " + comment) if comment else "")]
            if keep else [])
        lines[node.lineno - 1:end_line] = replacement
    repaired = "\n".join(lines)
    try:
        ast.parse(repaired)
    except SyntaxError:
        return source, []  # the removal broke the module (e.g. emptied a block): leave it for the gates
    return repaired, sorted(removed)


def settle_changes(worktree: Path, base: str, plan: dict[str, Any], handback: dict[str, Any]) -> list[str]:
    """Keep only the planned module and the worker's own tests; everything else the model emitted is discarded.

    A module written under its bare file name is moved to the planned path, and the
    generated tests are restored byte for byte, so the model can never weaken its spec.
    """
    module, test = plan["module_path"], plan["test_path"]
    stray = [p for p in changed_paths(worktree) if p not in (module, test)]
    target = worktree / module
    misplaced = [p for p in stray if p.rsplit("/", 1)[-1] == module.rsplit("/", 1)[-1]
                 and _git(worktree, "cat-file", "-e", f"{base}:{p}").returncode != 0]
    if len(misplaced) == 1 and (not target.is_file() or not target.read_text(encoding="utf-8").strip()):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((worktree / misplaced[0]).read_bytes())
        handback["recovered_from"] = misplaced[0]
    handback["discarded_paths"] = sorted({*handback.get("discarded_paths", []), *stray})
    for _attempt in range(10):  # an exiting aider helper can briefly hold a file open on Windows
        tracked = [p for p in stray if _git(worktree, "cat-file", "-e", f"{base}:{p}").returncode == 0]
        if tracked:
            _git(worktree, "checkout", base, "--", *tracked)
        untracked = [p for p in stray if p not in tracked]
        if untracked:
            # aider stages a file it creates (2026-11-02, VoiceStudio S2: "AM singing_project.py"); `git clean` never
            # removes an indexed path, so unstage first or the stray survives every retry and fails the scope gate
            _git(worktree, "rm", "--cached", "-f", "-q", "--ignore-unmatch", "--", *untracked)
            _git(worktree, "clean", "-fdq", "--", *untracked)
        stray = [p for p in changed_paths(worktree) if p not in (module, test)]
        if not stray:
            break
        time.sleep(1.0)
    spec = render_tests(plan)
    if (worktree / test).read_text(encoding="utf-8") != spec:
        (worktree / test).write_text(spec, encoding="utf-8")
        handback["tests_restored"] = True
    return changed_paths(worktree)


def production_gates(worktree: Path, base: str, paths: list[str]) -> str | None:
    from scripts.autonomous_slice_builder import generated_change_failure

    return generated_change_failure(worktree, base, paths)


ENV_INSTALL_SECONDS = 3600
ENV_RECIPE = "2"  # bump when the install recipe changes, so existing environments are rebuilt (2: git pins)


def poetry_lock_pins(lock: Path) -> list[str]:
    """``name==version`` for every locked package (all groups) whose markers apply to this platform."""
    import tomllib

    try:
        from packaging.markers import Marker
    except ImportError:  # without packaging, keep every pin; pip rejects inapplicable wheels itself
        Marker = None
    pins, indexes = [], set()
    for package in tomllib.loads(lock.read_text(encoding="utf-8")).get("package", []):
        marker = package.get("markers")
        if isinstance(marker, dict):  # per-group markers: apply if any group's marker applies
            marker = " or ".join(f"({m})" for m in marker.values() if m)
        if Marker is not None and isinstance(marker, str) and marker.strip():
            try:
                if not Marker(marker).evaluate():
                    continue
            except Exception:
                pass
        source = package.get("source") or {}
        commit = str(source.get("resolved_reference") or "")
        if source.get("type") == "git" and str(source.get("url", "")).startswith("https://") and re.fullmatch(
                r"[0-9a-f]{40}", commit):
            # an exact-commit VCS pin is reproducible (measured: knowledge-engine-web's mypy failed only
            # because its pinned knowledge-engine-ai dependency was skipped)
            pins.append(f"{package['name']} @ git+{source['url']}@{commit}")
            continue
        if source.get("type") in {"directory", "file", "git", "url"}:
            continue  # local/VCS sources are not reproducible from an index
        if source.get("type") == "legacy" and source.get("url"):
            indexes.add(f"--extra-index-url {source['url']}")  # e.g. the PyTorch CPU wheel index
        pins.append(f"{package['name']}=={package['version']}")
    return [*sorted(indexes), *sorted(pins)]


def repository_environment(worktree: Path, check: Callable[[], None]) -> dict[str, Any]:
    """A persistent installed environment per repository, shared by every turn's worktree.

    A fresh worktree has no virtualenv, so a Poetry or uv preflight would create an
    empty one and fail (measured: ``No module named ruff``). The environment lives
    beside the lane's clone and is reinstalled only when the lockfile changes.
    """
    import hashlib

    common = _git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    clone = Path(common).parent
    kind = "poetry" if (worktree / "poetry.lock").is_file() else "uv" if (worktree / "uv.lock").is_file() else None
    record: dict[str, Any] = {"kind": kind or "system", "overrides": {}, "python": sys.executable}
    if kind is None:
        return record
    envdir = clone.parent / ".envs" / clone.name
    scripts = envdir / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    digest = hashlib.sha256(ENV_RECIPE.encode() + b"".join(
        (worktree / name).read_bytes() for name in (f"{kind}.lock", "pyproject.toml") if (worktree / name).is_file()
    )).hexdigest()
    overrides = {"VIRTUAL_ENV": str(envdir), "PATH": str(scripts) + os.pathsep + os.environ.get("PATH", ""),
                 "POETRY_VIRTUALENVS_CREATE": "false", "UV_PROJECT_ENVIRONMENT": str(envdir)}
    record.update(overrides=overrides, python=str(python), envdir=str(envdir), lock_sha256=digest)
    marker = envdir / ".iron-god-env.json"
    try:
        if json.loads(marker.read_text(encoding="utf-8")).get("lock_sha256") == digest and python.is_file():
            record["status"] = "REUSED"
            return record
    except (OSError, ValueError):
        pass
    if not python.is_file():
        envdir.parent.mkdir(parents=True, exist_ok=True)
        import subprocess

        made = subprocess.run([sys.executable, "-m", "venv", str(envdir)], capture_output=True, text=True,
                              check=False, timeout=600)
        if made.returncode != 0:
            raise RuntimeError("ENVIRONMENT_VENV_FAILED: " + made.stderr[-300:])
    if kind == "poetry":
        # Poetry's own installer cannot reach PyPI on this machine while pip can (measured), so the
        # exact versions pinned in poetry.lock are installed with the environment's pip.
        requirements = envdir / "iron-god-lock-requirements.txt"
        requirements.write_text("\n".join(poetry_lock_pins(worktree / "poetry.lock")) + "\n", encoding="utf-8")
        argv = (str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(requirements))
    else:
        argv = ("uv", "sync", "--frozen")
    result = NativeWindowsRuntime().execute_supervised(
        RuntimeRequest(argv, worktree, {**os.environ, **overrides}, ENV_INSTALL_SECONDS), check, interval=2.0,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ENVIRONMENT_INSTALL_FAILED: {_tail((result.stdout or '') + (result.stderr or ''))[-400:]}")
    marker.write_text(json.dumps({"lock_sha256": digest, "kind": kind}), encoding="utf-8")
    record["status"] = "INSTALLED"
    return record


def production_preflight(worktree: Path, check: Callable[[], None], timeout: int) -> dict[str, Any]:
    temp = worktree.parent / f"{worktree.name}.tmp"
    try:
        environment = repository_environment(worktree, check)
    except RuntimeError as exc:
        if "EXECUTION_AUTHORITY_REVOKED" in str(exc):
            raise
        return {"command": "prepare repository environment", "returncode": None, "ok": False,
                "output_tail": str(exc)[:1500], "environment": "FAILED"}
    release_builder_model()  # the full test run and mypy must not overlap the resident model
    env = private_temp_env({**os.environ, **environment["overrides"], **PREFLIGHT_CONSOLE,
                            "PYTHONPATH": os.pathsep.join([str(worktree / "src"), str(worktree),
                                                           os.environ.get("PYTHONPATH", "")])}, temp)
    try:
        result = NativeWindowsRuntime().execute_supervised(
            RuntimeRequest((environment["python"], *PREFLIGHT_COMMAND), worktree, env, timeout), check, interval=2.0,
        )
        return {"command": "python engineering/preflight.py", "returncode": result.returncode,
                "ok": result.returncode == 0, "environment": f"{environment['kind']}:{environment.get('status', '-')}",
                "output_tail": _tail((result.stdout or "") + "\n" + (result.stderr or ""))}
    finally:
        remove_tree(temp)


def execute_product_packet(
    packet: dict[str, Any],
    *,
    authority: Callable[[], bool],
    plan: Callable[[Path, dict[str, Any], Callable[[], None], str], Any] = production_planner,
    run_agent: Callable[[Path, str, list[str], Callable[[], None]], Any] = production_agent,
    gates: Callable[[Path, str, list[str]], str | None] = production_gates,
    preflight: Callable[[Path, Callable[[], None], int], dict[str, Any]] = production_preflight,
    format_files: Callable[[Path, list[str], Callable[[], None]], str] = production_format,
    rederive: Callable[[dict[str, Any], dict[str, Any], str], Any] | None = None,
    generate: Callable[[str, int, Callable[[], None]], dict[str, Any]] | None = None,
    directed_tests: Callable[[Path, str, str, Callable[[], None]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Plan and implement one product turn; returns the Canon handback (never raises for work failures)."""
    worktree = Path(packet["worktree"])
    base = packet["base_sha"]
    handback: dict[str, Any] = {
        "schema_version": 1, "packet_id": packet["packet_id"], "project": packet["project"],
        "repository": packet["repository"], "branch": packet["branch"], "head_sha": base,
        "probe_id": packet["packet_id"], "pattern_id": "iron-god-product-turn",
        "evidence_ref": f"local://iron-god-products/{packet['packet_id']}/handback",
        "observed_at": _now(), "status": "ENVIRONMENT_FAILURE", "summary": "",
        "changed_paths": [], "verification": [], "note": "", "transcript_tail": "", "plan": None,
        "plan_rejections": [],
    }

    def finish(status: str, summary: str) -> dict[str, Any]:
        head = _git(worktree, "rev-parse", "HEAD")
        handback.update(status=status, summary=summary, observed_at=_now(),
                        head_sha=head.stdout.strip() if head.returncode == 0 else base)
        return handback

    if not authority():
        return finish("ENVIRONMENT_FAILURE", "EXECUTION_AUTHORITY_DENIED_BEFORE_START")
    head = _git(worktree, "rev-parse", "HEAD")
    branch = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != base:
        return finish("ENVIRONMENT_FAILURE", "WORKTREE_NOT_AT_EXACT_BASE")
    if branch.stdout.strip() != packet["branch"] or changed_paths(worktree):
        return finish("ENVIRONMENT_FAILURE", "WORKTREE_IDENTITY_OR_CLEANLINESS_UNVERIFIED")

    def check() -> None:
        if not authority():
            raise RuntimeError("EXECUTION_AUTHORITY_REVOKED")

    repair = packet.get("repair")
    if repair:  # a bounded review repair of an open pull request's module, on its exact head
        try:
            return execute_review_repair(packet, repair, worktree, base, handback, finish, authority=authority,
                                         check=check, run_agent=run_agent, gates=gates, preflight=preflight,
                                         format_files=format_files)
        except RuntimeError as exc:
            if "EXECUTION_AUTHORITY_REVOKED" in str(exc):
                return finish("ENVIRONMENT_FAILURE", "EXECUTION_AUTHORITY_REVOKED_DURING_REVIEW_REPAIR")
            return finish("ENVIRONMENT_FAILURE", f"REVIEW_REPAIR_FAILED_TO_RUN: {str(exc)[:300]}")
        except Exception as exc:  # the handback is always written
            return finish("ENVIRONMENT_FAILURE", f"REVIEW_REPAIR_FAILED_TO_RUN: {type(exc).__name__}")

    direct = (packet.get("slice") or {}).get("direct")
    if direct:  # an approved Level 2 slice: a directed edit of named units in one existing module
        try:
            directed_plan = validate_direct(direct, worktree, base, packet)
        except PlanRejected as exc:
            handback["plan_rejections"] = [str(exc)]
            return finish("FAIL", f"BACKLOG_SLICE_INVALID: {exc}"[:400])
        handback["plan"] = {k: v for k, v in directed_plan.items() if k != "test_source"}
        handback["note"] = directed_plan["note"]
        try:
            return execute_directed_slice(packet, directed_plan, worktree, base, handback, finish,
                                          authority=authority, check=check, generate=generate or production_generate,
                                          tests=directed_tests or directed_checks, gates=gates, preflight=preflight,
                                          format_files=format_files)
        except RuntimeError as exc:
            if "EXECUTION_AUTHORITY_REVOKED" in str(exc):
                return finish("ENVIRONMENT_FAILURE", "EXECUTION_AUTHORITY_REVOKED_DURING_DIRECTED_SLICE")
            return finish("ENVIRONMENT_FAILURE", f"DIRECTED_SLICE_FAILED_TO_RUN: {str(exc)[:300]}")
        except Exception as exc:  # an unreachable model or a broken environment; the handback is always written
            return finish("ENVIRONMENT_FAILURE",
                          f"DIRECTED_SLICE_FAILED_TO_RUN: {type(exc).__name__}: {str(exc)[:200]}")

    # An approved backlog slice is built exactly as written: the local model never re-plans it.
    approved = (packet.get("slice") or {}).get("build")
    try:
        feedback, rejections = "", []
        for _attempt in range(1 if approved else PLAN_ATTEMPTS):  # one bounded repair for a self-planned slice
            try:
                slice_plan = validate_plan(approved or plan(worktree, packet, check, feedback), worktree, base,
                                           packet)
                break
            except PlanRejected as exc:
                rejections.append(str(exc))
                feedback = f"Your previous plan was rejected: {exc}. Fix that and plan again."
            except ValueError as exc:  # malformed or truncated JSON reply
                rejections.append(f"PLAN_REPLY_NOT_JSON: {str(exc)[:120]}")
                feedback = "Your previous reply was not complete JSON. Reply with complete, shorter JSON only."
        else:
            handback["plan_rejections"] = rejections
            prefix = "BACKLOG_SLICE_INVALID" if approved else "PLAN_REJECTED"
            return finish("FAIL", f"{prefix}: {' | '.join(rejections)}"[:400])
        handback["plan_rejections"] = rejections
    except RuntimeError as exc:
        if "EXECUTION_AUTHORITY_REVOKED" in str(exc):
            return finish("ENVIRONMENT_FAILURE", "EXECUTION_AUTHORITY_REVOKED_DURING_PLANNING")
        return finish("ENVIRONMENT_FAILURE", f"PLANNER_FAILED: {str(exc)[:300]}")
    except Exception as exc:  # A malformed reply or an unreachable model is a failed plan, not a crash.
        return finish("FAIL", f"PLAN_REJECTED: {type(exc).__name__}: {str(exc)[:200]}")
    handback["plan"] = slice_plan
    handback["note"] = slice_plan["note"]
    module, test = slice_plan["module_path"], slice_plan["test_path"]
    (worktree / test).parent.mkdir(parents=True, exist_ok=True)
    (worktree / test).write_text(render_tests(slice_plan), encoding="utf-8")  # the spec, written by the worker
    try:
        run_agent(worktree, build_message(packet, slice_plan, worktree), [module], check)
    except RuntimeError as exc:
        if "EXECUTION_AUTHORITY_REVOKED" in str(exc):
            return finish("ENVIRONMENT_FAILURE", "EXECUTION_AUTHORITY_REVOKED_DURING_IMPLEMENTATION")
        return finish("ENVIRONMENT_FAILURE", f"AGENT_FAILED: {str(exc)[:300]}")
    except Exception as exc:  # The handback must always be written.
        return finish("ENVIRONMENT_FAILURE", f"AGENT_FAILED: {type(exc).__name__}")

    handback["transcript_tail"] = transcript_tail(worktree)
    for artifact in worktree.glob(".aider*"):
        remove_tree(artifact) if artifact.is_dir() else artifact.unlink()
    changed = settle_changes(worktree, base, slice_plan, handback)
    handback["changed_paths"] = changed
    if module not in changed or not (worktree / module).is_file():
        return finish("FAIL", "PLANNED_MODULE_NOT_PRODUCED")
    fixed, dropped = drop_builtin_typing_imports((worktree / module).read_text(encoding="utf-8"))
    if dropped:
        (worktree / module).write_text(fixed, encoding="utf-8", newline="\n")
        handback["sanitized_typing_imports"] = dropped
    if set(changed) != {module, test}:
        return finish("REVIEW_REQUIRED", "CHANGES_OUTSIDE_PLAN: " + ", ".join(sorted(set(changed) - {module, test})))
    foreign = stdlib_only_failure(worktree, module)
    if foreign:
        return finish("FAIL", f"STDLIB_GATE: {foreign}")
    quick = slice_checks(worktree, module, test, check)
    if not quick["ok"]:
        # One bounded repair: the model sees the exact failing assertions and may change only the module.
        handback["first_test_failure"] = quick["output_tail"][-1500:]
        try:
            run_agent(worktree, repair_message(slice_plan, quick["output_tail"]), [module], check)
        except RuntimeError as exc:
            if "EXECUTION_AUTHORITY_REVOKED" in str(exc):
                return finish("ENVIRONMENT_FAILURE", "EXECUTION_AUTHORITY_REVOKED_DURING_REPAIR")
            return finish("FAIL", f"REPAIR_FAILED: {str(exc)[:300]}")
        for artifact in worktree.glob(".aider*"):
            remove_tree(artifact) if artifact.is_dir() else artifact.unlink()
        if set(settle_changes(worktree, base, slice_plan, handback)) != {module, test}:
            return finish("REVIEW_REQUIRED", "REPAIR_CHANGED_MORE_THAN_THE_MODULE")
        foreign = stdlib_only_failure(worktree, module)
        if foreign:
            return finish("FAIL", f"STDLIB_GATE: {foreign}")
        quick = slice_checks(worktree, module, test, check)
        handback["repaired"] = True
    if not quick["ok"] and "pytest" in quick["command"]:
        corrected = adjudicate(worktree, packet, slice_plan, quick["output_tail"], check, rederive)
        if corrected:
            handback["adjudicated_examples"] = corrected
            (worktree / test).write_text(render_tests(slice_plan), encoding="utf-8")
            quick = slice_checks(worktree, module, test, check)
    handback["verification"] = [quick]
    if not quick["ok"]:
        return finish("FAIL", "SLICE_TESTS_FAILED")
    return commit_and_verify(packet, worktree, base, changed, [module, test],
                             f"iron-god: add {slice_plan['function_name']} ({packet['packet_id']})", quick, handback,
                             finish, authority=authority, check=check, gates=gates, preflight=preflight,
                             format_files=format_files)


REVIEW_FINDING_CHARS = 1200


def review_repair_message(repair: dict[str, Any]) -> str:
    """The recorded review findings, bounded, for one edit of the slice's module only."""
    module, test = repair["module_path"], repair["test_path"]
    def clean(text: Any) -> str:
        return " ".join(re.sub(r"<[^>]*>|!\[[^\]]*\]\([^)]*\)", "", str(text)).split())[:REVIEW_FINDING_CHARS]

    findings = []
    for row in repair.get("findings") or []:
        # every comment of the conversation, each bounded, so a current-head follow-up is never cut off
        comments = row.get("comments") or [{"body": row.get("body", ""), "on_current_head": False}]
        lines = [f"- {row.get('path')}:{row.get('line') or '?'}: {clean(comments[0].get('body', ''))}"]
        lines += [f"  follow-up{' on the current head' if c.get('on_current_head') else ''}: {clean(c.get('body', ''))}"
                  for c in comments[1:]]
        findings.extend(lines)
    return (f"Code review of pull request #{(repair.get('pr') or {}).get('number')} found problems in {module}. "
            f"Fix only {module}. Its tests in {test} are correct and must not change; keep them passing and keep "
            "the repository's type checker and linter clean. Address every finding:\n" + "\n".join(findings))


def execute_review_repair(packet: dict[str, Any], repair: dict[str, Any], worktree: Path, base: str,
                          handback: dict[str, Any], finish: Callable[[str, str], dict[str, Any]], *,
                          authority: Callable[[], bool], check: Callable[[], None],
                          run_agent: Callable[[Path, str, list[str], Callable[[], None]], Any],
                          gates: Callable[[Path, str, list[str]], str | None],
                          preflight: Callable[[Path, Callable[[], None], int], dict[str, Any]],
                          format_files: Callable[[Path, list[str], Callable[[], None]], str]) -> dict[str, Any]:
    """One directed review repair: only the slice's module changes, its tests stay byte-identical and pass,
    then the same commit, change gates and canonical preflight as any other turn. Never retried in a loop:
    the lane bounds the attempts and records each one."""
    module, test = str(repair.get("module_path") or ""), str(repair.get("test_path") or "")
    if (not module.endswith(".py") or module.startswith("tests/") or not _under(module, packet["paths"])
            or _under(module, packet["planning"]["forbidden_paths"]) or not test.startswith("tests/")):
        return finish("FAIL", f"REVIEW_REPAIR_MODULE_INVALID: {module[:120]}")
    if not (worktree / module).is_file() or not (worktree / test).is_file():
        return finish("FAIL", "REVIEW_REPAIR_FILES_MISSING")
    if not repair.get("findings"):
        return finish("FAIL", "REVIEW_REPAIR_HAS_NO_FINDINGS")
    test_bytes = (worktree / test).read_bytes()
    handback["plan"] = {"mode": "review_repair", "module_path": module, "test_path": test,
                        "objective": packet["task"], "findings": len(repair["findings"])}
    run_agent(worktree, review_repair_message(repair), [module], check)
    handback["transcript_tail"] = transcript_tail(worktree)
    for artifact in worktree.glob(".aider*"):
        remove_tree(artifact) if artifact.is_dir() else artifact.unlink()
    changed = changed_paths(worktree)
    handback["changed_paths"] = changed
    if (worktree / test).read_bytes() != test_bytes or set(changed) - {module}:
        return finish("REVIEW_REQUIRED", "REVIEW_REPAIR_CHANGED_MORE_THAN_THE_MODULE: " + ", ".join(sorted(changed)))
    if module not in changed:
        return finish("FAIL", "REVIEW_REPAIR_PRODUCED_NO_CHANGE")
    quick = slice_checks(worktree, module, test, check)
    handback["verification"] = [quick]
    if not quick["ok"]:
        return finish("FAIL", "REVIEW_REPAIR_TESTS_FAILED")
    return commit_and_verify(packet, worktree, base, [module], [module],
                             f"iron-god: repair review findings in {module} ({packet['packet_id']})", quick,
                             handback, finish, authority=authority, check=check, gates=gates, preflight=preflight,
                             format_files=format_files)


def commit_and_verify(packet: dict[str, Any], worktree: Path, base: str, changed: list[str], files: list[str],
                      message: str, quick: dict[str, Any], handback: dict[str, Any],
                      finish: Callable[[str, str], dict[str, Any]], *, authority: Callable[[], bool],
                      check: Callable[[], None], gates: Callable[[Path, str, list[str]], str | None],
                      preflight: Callable[[Path, Callable[[], None], int], dict[str, Any]],
                      format_files: Callable[[Path, list[str], Callable[[], None]], str]) -> dict[str, Any]:
    """Format, commit, the builder's change gates, then the canonical preflight at the exact head."""
    try:
        handback["formatted"] = format_files(worktree, files, check)
    except RuntimeError as exc:
        if "EXECUTION_AUTHORITY_REVOKED" in str(exc):
            return finish("ENVIRONMENT_FAILURE", "EXECUTION_AUTHORITY_REVOKED_DURING_FORMAT")
        return finish("ENVIRONMENT_FAILURE", f"FORMAT_FAILED: {str(exc)[:300]}")
    if not authority():
        return finish("ENVIRONMENT_FAILURE", "EXECUTION_AUTHORITY_REVOKED_BEFORE_COMMIT")
    _git(worktree, "add", "--", *changed)
    commit = _git(worktree, "-c", "user.name=Iron God Product Worker", "-c", "user.email=iron-god-product@localhost",
                  "commit", "-q", "-m", message)
    if commit.returncode != 0:
        return finish("ENVIRONMENT_FAILURE", "COMMIT_FAILED: " + _tail(commit.stderr)[:300])
    gate = gates(worktree, base, changed)
    if gate:
        return finish("REVIEW_REQUIRED", f"CHANGE_GATE: {gate}"[:400])
    try:
        result = preflight(worktree, check, int(packet.get("preflight_timeout_s", 7200)))
    except RuntimeError as exc:
        if "EXECUTION_AUTHORITY_REVOKED" in str(exc):
            return finish("ENVIRONMENT_FAILURE", "EXECUTION_AUTHORITY_REVOKED_DURING_PREFLIGHT")
        return finish("ENVIRONMENT_FAILURE", f"PREFLIGHT_FAILED_TO_RUN: {str(exc)[:300]}")
    handback["verification"] = [quick, result]
    return finish("PASS" if result["ok"] else "FAIL",
                  "canonical preflight passed at the exact head" if result["ok"] else "canonical preflight failed")


DIRECT_TEST_CHARS = 6000
DIRECT_MAX_UNITS = 3
DIRECT_NUM_CTX = 8192


def validate_direct(spec: Any, worktree: Path, base: str, packet: dict[str, Any]) -> dict[str, Any]:
    """Fail-closed checks of an approved Level 2 direction before the model is called."""
    from project_orchestrator import iron_god_directed_edit as de

    if not isinstance(spec, dict):
        raise PlanRejected("DIRECT_NOT_AN_OBJECT")
    module = str(spec.get("module_path") or "").replace("\\", "/")
    test = str(spec.get("test_path") or "").replace("\\", "/")
    forbidden = [*packet["planning"]["forbidden_paths"], *(packet.get("avoid_paths") or [])]
    for path in (module, test):
        if not path.endswith(".py") or path.startswith("/") or ".." in path.split("/"):
            raise PlanRejected(f"DIRECT_PATH_INVALID: {path[:120]}")
        if _under(path, forbidden):
            raise PlanRejected(f"DIRECT_PATH_FORBIDDEN_OR_UNDER_REVIEW: {path}")
    if not _under(module, packet["paths"]) or module.startswith("tests/"):
        raise PlanRejected("DIRECT_MODULE_OUTSIDE_ENVELOPE")
    if not test.startswith("tests/") or not test.rsplit("/", 1)[-1].startswith("test_"):
        raise PlanRejected("DIRECT_TEST_PATH_INVALID")
    if _git(worktree, "cat-file", "-e", f"{base}:{module}").returncode != 0:
        raise PlanRejected(f"DIRECT_MODULE_MISSING: {module}")
    approved_base = str(spec.get("approved_base") or "")
    if approved_base and not re.fullmatch(r"[0-9a-f]{40}", approved_base):
        raise PlanRejected("DIRECT_APPROVED_BASE_INVALID")
    on_main = _git(worktree, "show", f"{base}:{test}")
    # the same approved test already on main (another developer merged it) is checked, never overwritten
    if on_main.returncode == 0 and on_main.stdout.replace("\r\n", "\n") != str(spec.get("test_source") or ""):
        raise PlanRejected(f"DIRECT_TEST_WOULD_OVERWRITE: {test}")
    targets = [str(t) for t in spec.get("targets") or []]
    add = [{"name": str(a.get("name")), "after": str(a.get("after")), "spec": str(a.get("spec") or "")}
           for a in spec.get("add") or [] if isinstance(a, dict)]
    if not targets or len(targets) + len(add) > DIRECT_MAX_UNITS:
        raise PlanRejected("DIRECT_UNITS_OUT_OF_BOUNDS")
    direction = " ".join(str(spec.get("direction") or "").split())
    if not 20 <= len(direction) <= MAX_TEXT:
        raise PlanRejected("DIRECT_DIRECTION_INCOMPLETE")
    source = str(spec.get("test_source") or "")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        raise PlanRejected("DIRECT_TEST_UNPARSEABLE") from None
    if len(source) > DIRECT_TEST_CHARS or not any(
            isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in tree.body):
        raise PlanRejected("DIRECT_TEST_INVALID")
    found = de.units((worktree / module).read_text(encoding="utf-8"))
    unknown = [t for t in targets if t not in found] + [a["name"] for a in add if a["name"] in found
                                                        or a["after"] not in found or not a["name"].isidentifier()]
    if unknown:
        raise PlanRejected(f"DIRECT_UNITS_UNKNOWN: {unknown}")
    return {"mode": "directed", "module_path": module, "test_path": test, "targets": targets, "add": add,
            "direction": direction, "test_source": source, "objective": direction,
            "approved_base": approved_base, "test_on_main": on_main.returncode == 0,
            "note": " ".join(str(spec.get("note") or "").split())[:NOTE_CHARS]}


def external_completion(worktree: Path, base: str, plan: dict[str, Any]) -> dict[str, Any] | None:
    """Evidence that another developer completed this slice: its approved tests, verified to fail at
    ``approved_base``, now pass on main, and main changed the module since then. Never inferred from a
    pull request title or the slice id alone; without an approval base nothing is claimed."""
    approved = plan.get("approved_base") or ""
    if not approved or _git(worktree, "merge-base", "--is-ancestor", approved, base).returncode != 0:
        return None
    commits = _git(worktree, "log", "--format=%H", f"{approved}..{base}", "--", plan["module_path"]).stdout.split()
    if not commits:
        return None
    return {"approved_base": approved, "verified_base": base, "module_commits": commits[:20],
            "summary": f"the approved tests pass on {base[:12]}; {plan['module_path']} changed in "
                       f"{len(commits)} commit(s) since {approved[:12]}, latest {commits[0][:12]}"}


def production_generate(prompt: str, num_predict: int, check: Callable[[], None]) -> dict[str, Any]:
    """One plain completion from the local builder model, holding the shared model lock."""
    import shutil

    from scripts.autonomous_slice_builder import MODEL, _acquire_ollama_lock

    check()
    base = os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434").rstrip("/")
    body = {"model": MODEL.split("/", 1)[-1], "prompt": prompt, "stream": False,
            "options": {"temperature": 0.2, "num_ctx": DIRECT_NUM_CTX, "num_predict": num_predict}}
    lock = _acquire_ollama_lock()
    try:
        request = urllib.request.Request(base + "/api/generate", data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=1800) as response:
            reply = json.load(response)
    finally:
        shutil.rmtree(lock, ignore_errors=True)
    check()
    return {"text": reply.get("response") or "", "prompt_tokens": reply.get("prompt_eval_count"),
            "output_tokens": reply.get("eval_count"), "model_s": round((reply.get("total_duration") or 0) / 1e9, 1)}


def repository_test(worktree: Path, test: str, check: Callable[[], None], timeout: int = 600) -> dict[str, Any]:
    """The slice's tests in the repository's own installed environment (an existing module has real imports)."""
    environment = repository_environment(worktree, check)
    temp = worktree.parent / f"{worktree.name}.quick.tmp"
    env = private_temp_env({**os.environ, **environment["overrides"], "PYTHONDONTWRITEBYTECODE": "1",
                            "PYTHONPATH": os.pathsep.join([str(worktree / "src"), str(worktree)])}, temp)
    try:
        result = NativeWindowsRuntime().execute_supervised(RuntimeRequest(
            (environment["python"], "-m", "pytest", "-q", "-p", "no:cacheprovider", test), worktree, env, timeout),
            check, interval=2.0)
        return {"command": f"python -m pytest -q {test}", "returncode": result.returncode,
                "ok": result.returncode == 0, "output_tail": _tail((result.stdout or "") + "\n" + (result.stderr or ""))}
    finally:
        remove_tree(temp)


def directed_checks(worktree: Path, module: str, test: str, check: Callable[[], None]) -> dict[str, Any]:
    """The slice's tests, then the repository's type checker and linter, on the changed module and its tests."""
    tests = repository_test(worktree, test, check)
    if not tests["ok"]:
        return tests
    for result, marker in ((type_check(worktree, [module, test], check), ": error:"),
                           (lint_check(worktree, [module, test], check), ".py:")):
        if result is not None and not result["ok"]:
            lines = [line for line in result["output_tail"].splitlines() if marker in line][:20]
            return {**result, "output_tail": "\n".join(lines) or result["output_tail"]}
    return tests


def failing_assertions(tail: str, limit: int = 40) -> str:
    keep = [line for line in tail.splitlines() if line.startswith(("E ", ">", "FAILED", "ERROR"))]
    return "\n".join(keep[:limit]) or tail[-1500:]


def execute_directed_slice(packet: dict[str, Any], plan: dict[str, Any], worktree: Path, base: str,
                           handback: dict[str, Any], finish: Callable[[str, str], dict[str, Any]], *,
                           authority: Callable[[], bool], check: Callable[[], None],
                           generate: Callable[[str, int, Callable[[], None]], dict[str, Any]],
                           tests: Callable[[Path, str, str, Callable[[], None]], dict[str, Any]],
                           gates: Callable[[Path, str, list[str]], str | None],
                           preflight: Callable[[Path, Callable[[], None], int], dict[str, Any]],
                           format_files: Callable[[Path, list[str], Callable[[], None]], str]) -> dict[str, Any]:
    """One approved Level 2 slice: a directed function-level edit, built once and never retried in a loop.

    Measured 2026-10-28 (calibration): directed edits passed 9/12 historical fixes first try with
    zero test tampering, and the repair round rescued none of the failures (the model re-emitted
    the same answer). A failed slice therefore goes back to the planner for decomposition.
    """
    from project_orchestrator import iron_god_directed_edit as de

    module, test = plan["module_path"], plan["test_path"]
    if not plan.get("test_on_main"):
        (worktree / test).parent.mkdir(parents=True, exist_ok=True)
        (worktree / test).write_text(plan["test_source"], encoding="utf-8")  # the approved spec, never the model's
    spec_bytes = (worktree / test).read_bytes()
    before = tests(worktree, module, test, check)
    if before["ok"]:
        external = external_completion(worktree, base, plan)
        if external is not None:  # nothing to build: recorded as completed elsewhere, with its evidence
            handback["external_completion"] = external
            return finish("FAIL", f"COMPLETED_EXTERNALLY: {external['summary']}")
        return finish("FAIL", "DIRECTED_TEST_NOT_DISCRIMINATING: the approved tests already pass on main")
    if plan.get("test_on_main"):
        return finish("REVIEW_REQUIRED", "NEEDS_REVALIDATION: the approved tests are already on main and fail there")
    original = (worktree / module).read_text(encoding="utf-8")
    found = de.units(original)
    lines = sum(found[t].end - found[t].start + 1 for t in plan["targets"])
    prompt = de.build_prompt(original, module, plan["direction"], plan["targets"], add=plan["add"],
                             failure=failing_assertions(before["output_tail"]), rules=quality_rules(worktree))
    reply = generate(prompt, min(4096, max(600, 30 * (lines + 25 * len(plan["add"])))), check)
    text = str(reply.get("text") or "")
    handback["directed"] = {**{k: reply.get(k) for k in ("prompt_tokens", "output_tokens", "model_s")},
                            "model": os.environ.get("EXPONENTIAL_BUILDER_MODEL", "ollama/qwen2.5-coder:7b"),
                            "prompt": prompt[:16000], "reply": text[:8000]}
    try:
        edited = de.apply_reply(original, text, plan["targets"], add=plan["add"])
    except de.DirectedEditError as exc:
        handback["directed"]["apply_error"] = str(exc)[:300]
        return finish("FAIL", f"DIRECTED_REPLY_REJECTED: {str(exc)[:300]}")
    (worktree / module).write_text(edited, encoding="utf-8")
    changed = changed_paths(worktree)
    handback["changed_paths"] = changed
    if set(changed) != {module, test} or (worktree / test).read_bytes() != spec_bytes:
        return finish("REVIEW_REQUIRED", "DIRECTED_CHANGES_OUTSIDE_PLAN: " + ", ".join(sorted(changed)))
    quick = tests(worktree, module, test, check)
    handback["verification"] = [quick]
    if not quick["ok"]:
        handback["directed"]["test_failure"] = failing_assertions(quick["output_tail"])[:3000]
        return finish("FAIL", "SLICE_TESTS_FAILED")
    # (the repository's formatter may reflow the approved tests during the lint check; the model never writes files)
    return commit_and_verify(packet, worktree, base, changed, [module, test],
                             f"iron-god: change {', '.join(plan['targets'])} in {module} ({packet['packet_id']})",
                             quick, handback, finish, authority=authority, check=check, gates=gates,
                             preflight=preflight, format_files=format_files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Iron God single product-turn worker")
    parser.add_argument("--packet", required=True)
    parser.add_argument("--handback", required=True)
    args = parser.parse_args(argv)
    packet = json.loads(Path(args.packet).read_text(encoding="utf-8"))
    release = os.environ.get("IRON_GOD_ACCEPTED_SHA")

    def authority() -> bool:
        try:
            return evaluate_execution_authority(release_sha=release).permitted is True
        except Exception:
            return False

    handback = execute_product_packet(packet, authority=authority)
    _write(Path(args.handback), handback)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
