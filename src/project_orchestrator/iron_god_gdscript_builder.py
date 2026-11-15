"""Opt-in local GDScript slice builder; independent Godot tests own acceptance.

This module supplies a *bounded, deterministic task* to the already-tested
local generate/validate/repair/rollback loop. It is deliberately not wired
into dispatch until an approved Godot production project, measured Ollama
capability, owner gate, and exact-head release can be checked. No legacy C++
route is selected by this module and no Claude transport is available.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Callable

from . import iron_god_cpp_builder as bounded

REQUIRED_ROUTE = "LOCAL"
TEST_ENTRY = re.compile(r"(?m)^\s*extends\s+(?:SceneTree|MainLoop)\s*(?:#.*)?$")
EXIT_CALL = re.compile(r"\bquit\s*\(")


def _canonical(path: Any, *, suffix: str | None = None) -> bool:
    if not isinstance(path, str) or not path or "\\" in path or path.startswith("/"):
        return False
    if ":" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        return False
    return suffix is None or path.endswith(suffix)


def validate(request: dict[str, Any], workspace: Path) -> list[str]:
    """Fail closed on absent authority, missing oracle, or dangerous scope.

    Independent tests must already exist and be outside the editable module.
    GDScript is opt-in by explicit request metadata; no auto-translation from
    Unreal or C++ work is ever implied.
    """
    if not isinstance(request, dict):
        return ["REQUEST_NOT_OBJECT"]
    problems: list[str] = []
    if not isinstance(request.get("id"), str) or not request["id"].strip():
        problems.append("TASK_ID_REQUIRED")
    if not isinstance(request.get("objective"), str) or not request["objective"].strip():
        problems.append("OBJECTIVE_REQUIRED")
    if request.get("owner_approved") is not True:
        problems.append("OWNER_APPROVAL_REQUIRED")
    route = request.get("route")
    if not isinstance(route, dict) or route.get("builder") != REQUIRED_ROUTE:
        problems.append("LOCAL_ROUTE_REQUIRED")
    if type(request.get("capability_level")) is not int or request["capability_level"] != 1:
        problems.append("MEASURED_LEVEL_ONE_REQUIRED")
    spec = request.get("gdscript")
    if not isinstance(spec, dict):
        return [*problems, "GDSCRIPT_SPEC_MISSING"]
    project, module, test = (spec.get(k) for k in ("project_root", "module", "test_script"))
    if not _canonical(project) or not _canonical(module, suffix=".gd") or not _canonical(test, suffix=".gd"):
        return [*problems, "NONCANONICAL_GODOT_PATH"]
    if module == test:
        problems.append("TEST_MUST_BE_INDEPENDENT")
    prefix = project.rstrip("/") + "/"
    if not module.startswith(prefix) or not test.startswith(prefix):
        problems.append("PATH_OUTSIDE_GODOT_PROJECT")
    # Canonical lexical paths are not sufficient when checkout subdirectories
    # are symlinks. Refuse targets that resolve outside the isolated workspace.
    root = workspace.resolve()
    for target in (workspace / project, (workspace / module).parent, workspace / test):
        if not target.resolve().is_relative_to(root):
            problems.append("PATH_ESCAPES_WORKSPACE")
            break
    # A generated module can be changed. The approved independent test cannot.
    if request.get("allowed_paths") != [module]:
        problems.append("ONLY_DECLARED_GDSCRIPT_MODULE_EDITABLE")
    if not isinstance(request.get("slice_acceptance"), list) or not request["slice_acceptance"]:
        problems.append("INDEPENDENT_ACCEPTANCE_MISSING")
    if not (workspace / project / "project.godot").is_file():
        problems.append("GODOT_PROJECT_MISSING")
    test_path = workspace / test
    if not test_path.is_file():
        problems.append("APPROVED_TEST_SCRIPT_MISSING")
    else:
        try:
            source = test_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            problems.append("APPROVED_TEST_UNREADABLE")
        else:
            if not TEST_ENTRY.search(source) or not EXIT_CALL.search(source):
                problems.append("TEST_MUST_BE_EXECUTABLE_SCENETREE")
    return problems


def toolchain_problems(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    return [] if which("godot") else ["GODOT_EXECUTABLE_UNAVAILABLE"]


def make_task(request: dict[str, Any], workspace: Path, *,
              which: Callable[[str], str | None] = shutil.which) -> bounded.Task:
    """Prepare a local GDScript edit with headless parse + independently fixed test.

    The script's exit code is the acceptance oracle; no model can rewrite it.
    Callers still must provide owner authority, measured Ollama admission,
    shared runtime lease, exact-head preflight, PR CI and release gates.
    """
    problems = validate(request, workspace) + toolchain_problems(which)
    if problems:
        raise ValueError("GDSCRIPT_TASK_BLOCKED: " + ", ".join(problems))
    spec = request["gdscript"]
    binary = which("godot")
    if binary is None:
        raise ValueError("GDSCRIPT_TASK_BLOCKED: GODOT_EXECUTABLE_UNAVAILABLE")
    project = workspace / spec["project_root"]
    module = spec["module"]
    test = spec["test_script"]
    # Paths are res://-relative to the *project*, not repository-relative.
    local_module = module[len(spec["project_root"]) + 1:]
    local_test = test[len(spec["project_root"]) + 1:]
    return bounded.Task(
        id=str(request["id"]),
        objective=str(request.get("objective") or "") + "\nAcceptance: " +
                  "; ".join(str(line) for line in request["slice_acceptance"]),
        workspace=workspace,
        allowed_paths=[module], forbidden_paths=[test],
        files=[module], context_files=[test],
        # Check only parses the production script; it cannot replace the
        # independent executable test (which must exit nonzero on failure).
        build=[binary, "--headless", "--path", str(project),
               "--script", local_module, "--check-only"],
        test=[binary, "--headless", "--path", str(project),
              "--script", local_test],
        rules=[
            "Write exactly the authorized GDScript module; never write a test, scene, project setting or asset.",
            "The immutable, existing SceneTree test script is the independent behavioral acceptance oracle.",
            "Use typed GDScript when the existing module or approved test contract requires it.",
            "Do not change simulation truth, mechanical behavior, acceptance criteria or owner controls.",
        ],
        language="GDScript",
    )
