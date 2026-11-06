"""Local C++ builder: generate, compile, test and repair a bounded change in an isolated workspace, with no external AI.

The model is only the *author*. Everything that decides whether the work is acceptable is deterministic code:

* **Scope** -- every file the model writes must match the task's ``allowed_paths`` and not its ``forbidden_paths``; a
  write outside them is rejected before it touches disk.
* **Feedback** -- the change is compiled and tested by the repository's own tools. The compiler's diagnostics and the
  tests' output, parsed and truncated, are the *only* thing fed back to the model for repair.
* **Limits** -- at most ``max_iterations`` model calls, a per-step timeout, a memory admission check before every model
  call, and an identical-failure detector: the same diagnostics twice in a row end the task instead of burning more time.
* **Rollback** -- on any non-success the workspace is restored (tracked files reset, files the builder created removed).
* **Evidence** -- every iteration is recorded (prompt/response sizes, timings, diagnostics fingerprint, files, results).

The model call, the process runner, the clock and the memory probe are injected, so the loop is tested without a model
or a compiler. ``scripts/iron_god_cpp_task.py`` binds them to a local Ollama model and the real toolchain.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .iron_god_roadmap_refill import _matches

SUCCESS = "SUCCESS"
FILE_BLOCK = re.compile(r"^=== FILE: (?P<path>[^\n=]+?) ===\s*\n(?P<body>.*?)(?=^=== FILE: |^=== END ===|\Z)", re.M | re.S)
FENCE = re.compile(r"^```[A-Za-z+]*\n(?P<code>.*?)^```\s*$", re.M | re.S)
# MSVC "path(12,5): error C2065: message" and GCC/Clang "path:12:5: error: message"
DIAGNOSTIC = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^\n:(]+?)(?:\((?P<line>\d+)(?:,\d+)?\)|:(?P<gline>\d+):\d+)\s*:\s*(?:fatal\s+)?error\s*(?P<code>[A-Z]*\d*)?\s*:?\s*(?P<msg>.+)$",
    re.M,
)
LINKER = re.compile(r"^.*\b(?:LNK\d{4}|undefined reference)\b.*$", re.M)
TEST_FAILURE = re.compile(r"^.*(?:assert|Assertion|FAILED|Failed|\*\*\*Failed|Test #\d+: .*Failed).*$", re.M)


@dataclass(frozen=True)
class Limits:
    max_iterations: int = 4
    model_timeout_s: int = 900
    build_timeout_s: int = 600
    test_timeout_s: int = 300
    min_free_gib: float = 7.0          # a 7B model (5.3 GB resident) plus headroom for Windows and the desktop
    memory_waits: int = 6              # bounded waits for memory, none of which consumes an attempt
    memory_wait_s: float = 20.0
    max_diagnostic_lines: int = 25
    max_context_chars: int = 9000


@dataclass
class Task:
    id: str
    objective: str
    workspace: Path
    allowed_paths: list[str]
    forbidden_paths: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)            # the files the change must create or edit
    context_files: list[str] = field(default_factory=list)    # files shown to the model, in order
    build: list[str] = field(default_factory=list)            # command that compiles the change
    test: list[str] = field(default_factory=list)             # command that runs the checks
    prelude: list[list[str]] = field(default_factory=list)    # commands run once first
    configure: list[list[str]] = field(default_factory=list)  # run after every write, before the build (e.g. cmake configure)
    rules: list[str] = field(default_factory=list)


Ask = Callable[[str], str]
Run = Callable[[list[str], Path, int], tuple[int, str]]


def parse_files(response: str) -> dict[str, str]:
    """Files from ``=== FILE: path ===`` blocks; a single fenced code block per file is unwrapped."""
    files: dict[str, str] = {}
    for found in FILE_BLOCK.finditer(response):
        body = found.group("body")
        fenced = FENCE.search(body)
        files[found.group("path").strip().replace("\\", "/")] = (fenced.group("code") if fenced else body).strip("\n") + "\n"
    return files


def scope_problems(paths: list[str], task: Task) -> list[str]:
    problems = []
    for path in paths:
        norm = path.replace("\\", "/")
        if norm.startswith("/") or ".." in norm.split("/") or re.match(r"^[A-Za-z]:", norm):
            problems.append(f"PATH_NOT_RELATIVE:{path}")
        elif not _matches(norm, task.allowed_paths) or _matches(norm, task.forbidden_paths):
            problems.append(f"OUT_OF_SCOPE:{path}")
    return problems


def diagnostics(output: str, limit: int) -> list[str]:
    """Compiler errors and linker failures, deduplicated and truncated; warnings and progress noise are dropped."""
    seen: list[str] = []
    for found in DIAGNOSTIC.finditer(output):
        message = re.sub(r"\s*\[[^\]]*\.vcxproj[^\]]*\]\s*$", "", found.group("msg").strip())  # MSVC appends the project
        text = f"{Path(found.group('file').strip()).name}({found.group('line') or found.group('gline')}): " \
               f"{found.group('code') or 'error'}: {message[:220]}"
        if text not in seen:
            seen.append(text)
    for found in LINKER.finditer(output):
        text = found.group(0).strip()[:240]
        if text not in seen:
            seen.append(text)
    return seen[:limit]


def fingerprint(lines: list[str]) -> str:
    """Identity of a failure that ignores line numbers and paths, so a repeat of the same mistake is recognised."""
    normal = sorted({re.sub(r"\(\d+\)", "", re.sub(r"[A-Za-z]:[\\/][^\s:]*", "", line)) for line in lines})
    return hashlib.sha256("\n".join(normal).encode()).hexdigest()[:16]


def _read(task: Task, spec: str, limit: int) -> str:
    """A context file, whole or as ``path#L12-40`` (inclusive line range), so a prompt carries the declarations it needs."""
    path, _, span = spec.partition("#L")
    try:
        text = (task.workspace / path).read_text(encoding="utf-8")
    except OSError:
        return ""
    if span:
        first, _, last = span.partition("-")
        lines = text.splitlines()
        text = "\n".join(lines[max(int(first) - 1, 0): int(last or first)])
    return text[:limit]


def initial_prompt(task: Task, limits: Limits) -> str:
    context = []
    budget = limits.max_context_chars
    for path in task.context_files:
        text = _read(task, path, budget)
        if text:
            context.append(f"--- existing file {path} ---\n{text}")
            budget -= len(text)
        if budget <= 0:
            break
    rules = "\n".join(f"- {rule}" for rule in task.rules)
    return (
        "You are a C++ engineer. Implement the change below exactly as specified. Output ONLY complete files in this "
        "format, with no other text:\n=== FILE: relative/path ===\n<entire file contents>\n=== FILE: next/path ===\n...\n"
        "=== END ===\n\n"
        f"TASK: {task.objective}\n\nFILES YOU MUST WRITE (complete contents, each exactly once): {', '.join(task.files)}\n"
        f"You may write only inside: {', '.join(task.allowed_paths)}\n"
        f"RULES:\n{rules}\n\nCONTEXT:\n" + "\n".join(context)
    )


def repair_prompt(task: Task, current: dict[str, str], stage: str, problems: list[str], output_tail: str) -> str:
    shown = "\n".join(f"=== FILE: {path} ===\n{text}" for path, text in current.items())
    return (
        f"Your previous attempt failed at the {stage} step. Fix it and output ONLY the complete corrected files in the "
        "same format (=== FILE: path === ... === END ===). Change only what the errors require; keep the public names.\n\n"
        f"TASK: {task.objective}\nYou may write only inside: {', '.join(task.allowed_paths)}\n\n"
        f"ERRORS ({stage}):\n" + "\n".join(problems or [output_tail[-1200:]]) + f"\n\nCURRENT FILES:\n{shown}"
    )


class MemoryGuard:
    """Continuous memory watchdog for the duration of a model call.

    Admission before a call is not enough on a shared desktop: available memory swings by gigabytes as other programs
    change. ``check`` is called repeatedly while the model runs; ``patience`` consecutive readings under ``floor_gib``
    mean pressure, and the caller aborts and unloads the model instead of letting the host start paging.
    """

    def __init__(self, available: Callable[[], float], floor_gib: float = 0.5, patience: int = 2) -> None:
        self.available, self.floor_gib, self.patience = available, floor_gib, patience
        self.low = 0
        self.lowest = available()

    def check(self) -> bool:
        """True when memory pressure is sustained and the model call must be aborted."""
        value = self.available()
        self.lowest = min(self.lowest, value)
        self.low = self.low + 1 if value < self.floor_gib else 0
        return self.low >= self.patience


def default_run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False)
        return done.returncode, (done.stdout or "") + (done.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"
    except OSError as exc:
        return 127, f"COULD_NOT_START: {exc}"


class Workspace:
    """Applies the builder's files and rolls everything back; the workspace must be an isolated git checkout."""

    def __init__(self, root: Path, run: Run) -> None:
        self.root, self.run, self.created = Path(root), run, []

    def write(self, files: dict[str, str]) -> None:
        for path, text in files.items():
            target = self.root / path
            existed = target.exists()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            if not existed and path not in self.created:
                self.created.append(path)

    def rollback(self) -> None:
        self.run(["git", "checkout", "--", "."], self.root, 120)
        for path in self.created:
            (self.root / path).unlink(missing_ok=True)
        self.created = []

    def diff(self) -> str:
        code, out = self.run(["git", "diff", "--no-color", "HEAD"], self.root, 120)
        added = []
        for path in self.created:
            if (self.root / path).is_file():
                added.append(f"+++ new file {path}\n" + (self.root / path).read_text(encoding="utf-8"))
        return (out if code == 0 else "") + "\n".join(added)


def build_change(task: Task, ask: Ask, *, run: Run = default_run, limits: Limits = Limits(),
                 free_gib: Callable[[], float] = lambda: 99.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Run the generate -> compile -> test -> repair loop. Returns the evidence record; ``status`` is SUCCESS only when the
    repository's own build and tests passed on the final files."""
    started = clock()
    workspace = Workspace(task.workspace, run)
    evidence: dict[str, Any] = {"task": task.id, "status": "NOT_STARTED", "iterations": [], "files": [],
                                "limits": limits.__dict__}

    def finish(status: str, reason: str = "") -> dict[str, Any]:
        evidence.update(status=status, reason=reason, seconds=round(clock() - started, 1))
        if status != SUCCESS:
            workspace.rollback()
            evidence["rolled_back"] = True
        else:
            evidence["diff"] = workspace.diff()
        return evidence

    for command in task.prelude:
        code, out = run(command, task.workspace, limits.build_timeout_s)
        if code != 0:
            return finish("PRELUDE_FAILED", out[-600:])
    prompt, current, last_fingerprint = initial_prompt(task, limits), {}, None
    for iteration in range(1, limits.max_iterations + 1):
        waits = 0
        while free_gib() < limits.min_free_gib:  # admission: no model call without memory; waiting spends no attempt
            if waits >= limits.memory_waits:
                return finish("WAITING_FOR_MEMORY", f"free {free_gib():.1f} GiB < {limits.min_free_gib} GiB")
            waits += 1
            sleep(limits.memory_wait_s)
        record: dict[str, Any] = {"n": iteration, "prompt_chars": len(prompt)}
        evidence["iterations"].append(record)
        t = clock()
        try:
            response = ask(prompt)
        except (OSError, TimeoutError, ValueError) as exc:  # a slow, absent or broken model ends the task with evidence
            record.update(model_seconds=round(clock() - t, 1), stage="MODEL", problems=[f"{type(exc).__name__}: {str(exc)[:200]}"])
            return finish("MODEL_UNAVAILABLE", record["problems"][0])
        record.update(model_seconds=round(clock() - t, 1), response_chars=len(response))
        files = parse_files(response)
        if not files:
            record["stage"] = "PARSE"
            prompt = repair_prompt(task, current, "output format", ["no '=== FILE: path ===' blocks were found"], response[:600])
            continue
        problems = scope_problems(list(files), task)
        missing = [path for path in task.files if path not in files]
        if problems or missing:
            record.update(stage="SCOPE", problems=problems + [f"MISSING_FILE:{m}" for m in missing])
            prompt = repair_prompt(task, current, "scope", record["problems"], "")
            if problems:
                continue  # nothing out of scope is ever written, even to be corrected later
        workspace.write({p: t for p, t in files.items() if p not in problems})
        current = {p: files[p] for p in files if p not in problems}
        evidence["files"] = sorted(current)
        code, out = 0, ""
        for command in [*task.configure, task.build]:
            code, out = run(command, task.workspace, limits.build_timeout_s)
            if code != 0:
                break
        record.update(build_exit=code)
        if code != 0:
            found = diagnostics(out, limits.max_diagnostic_lines)
            record.update(stage="BUILD", diagnostics=found)
            print_id = fingerprint(found or [out[-300:]])
            record["fingerprint"] = print_id
            if print_id == last_fingerprint:
                return finish("REPEATED_IDENTICAL_FAILURE", f"build failed identically twice: {print_id}")
            last_fingerprint = print_id
            prompt = repair_prompt(task, current, "compile", found, out)
            continue
        code, out = run(task.test, task.workspace, limits.test_timeout_s)
        record.update(test_exit=code, test_tail=out[-500:])
        if code == 0:
            record["stage"] = SUCCESS
            return finish(SUCCESS)
        failures = [line.strip()[:240] for line in TEST_FAILURE.findall(out)][: limits.max_diagnostic_lines]
        record.update(stage="TEST", diagnostics=failures)
        print_id = fingerprint(failures or [out[-300:]])
        record["fingerprint"] = print_id
        if print_id == last_fingerprint:
            return finish("REPEATED_IDENTICAL_FAILURE", f"tests failed identically twice: {print_id}")
        last_fingerprint = print_id
        prompt = repair_prompt(task, current, "test", failures, out)
    return finish("ITERATIONS_EXHAUSTED", f"{limits.max_iterations} model calls without a passing build and test run")


def record_json(evidence: dict[str, Any]) -> str:
    return json.dumps(evidence, indent=2, sort_keys=True, default=str)
