"""Fail fast on repository contracts a one-module local microtask cannot satisfy."""
from __future__ import annotations

from project_orchestrator import iron_god_task_decomposer as decomposition


def _request(*, companion: bool = True) -> dict:
    acceptance = ["a pure helper produces the required result"]
    if companion:
        acceptance.append("docs/STRUCTURE.md service count updated if a service file is added")
    acceptance.append("Canonical preflight, required CI and review pass at the exact head")
    return {
        "slice_acceptance": acceptance,
        "allowed_paths": ["backend/services/**", "tests/**", "docs/**"],
        "forbidden_paths": [],
        "objective": "Implement a local service helper",
    }


def _plan() -> dict:
    return {
        "subtasks": [{
            "key": "t1",
            "kind": "new",
            "title": "service helper",
            "module_path": "backend/services/new_helper.py",
            "function_name": "new_helper",
            "signature": "def new_helper(value: int) -> int:",
            "done_when": "the new helper returns the input plus one",
            "depends_on": [],
            "acceptance_examples": [
                {"call": "{fn}(1)", "expected": 2, "why": "adds one"},
                {"call": "{fn}(2)", "expected": 3, "why": "adds one"},
            ],
        }],
        "coverage": {"1": ["t1"]},
        "residual": [],
    }


def test_new_service_requiring_structure_count_does_not_enter_local_one_module_lane():
    plan, problems = decomposition.validate_plan(_plan(), _request(), exists=lambda _: False)
    assert plan is None
    assert any("STRUCTURE_COUNT_COMPANION_REQUIRED" in p for p in problems)
    assert any("docs/STRUCTURE.md" in p for p in problems)


def test_other_repos_or_scopes_are_not_blocked_by_an_inferred_documentation_requirement():
    plan, problems = decomposition.validate_plan(_plan(), _request(companion=False), exists=lambda _: False)
    assert plan is not None and not problems


def _edit_source(with_import: bool) -> str:
    prefix = "import pytest\n" if with_import else ""
    return prefix + "from pkg.core import one\n\ndef test_one_invalid():\n    with pytest.raises(ValueError):\n        one('invalid')\n"


def _edit(with_import: bool) -> dict:
    return {
        "key": "t1", "kind": "edit", "title": "validate an existing API",
        "module_path": "pkg/core.py", "test_path": "tests/test_core.py",
        "targets": ["one"], "test_source": _edit_source(with_import),
        "done_when": "one rejects invalid inputs with ValueError",
    }


def test_self_authored_pytest_test_must_import_pytest():
    request = {"allowed_paths": ["pkg/**", "tests/**"], "forbidden_paths": []}
    source = lambda _: "def one(value):\n    return value\n"
    exists = lambda path: path == "pkg/core.py"
    blocked, problems = decomposition._edit_subtask(
        _edit(False), "t1", request, exists, source, set(),
    )
    assert blocked is None and any("UNBOUND_PYTEST_REFERENCE" in p for p in problems)
    valid, problems = decomposition._edit_subtask(
        _edit(True), "t1", request, exists, source, set(),
    )
    assert valid is not None and not problems


def test_import_scope_is_respected_for_generated_tests():
    function_import = (
        "from pkg.core import one\n\ndef test_one():\n"
        "    import pytest\n    with pytest.raises(ValueError):\n"
        "        one('invalid')\n"
    )
    module_try_import = (
        "try:\n    import pytest\nexcept ImportError:\n    pass\n"
        "def test_one():\n    with pytest.raises(ValueError):\n"
        "        one('invalid')\n"
    )
    import_in_other_function = (
        "def helper():\n    import pytest\n\ndef test_one():\n"
        "    with pytest.raises(ValueError):\n        one('invalid')\n"
    )
    assert not decomposition._missing_pytest_import(function_import)
    assert not decomposition._missing_pytest_import(module_try_import)
    assert decomposition._missing_pytest_import(import_in_other_function)
