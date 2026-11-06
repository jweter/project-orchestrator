"""The local C++ builder loop: scope, compiler feedback, repair, limits, memory admission, rollback, evidence."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator import iron_god_cpp_builder as cb

MSVC_ERROR = (
    "C:\\ev\\core\\src\\simulation\\include\\x\\foo.hpp(12,5): error C2065: 'bar': undeclared identifier [C:\\ev\\b\\t.vcxproj]\n"
    "C:\\ev\\core\\src\\simulation\\tests\\foo_tests.cpp(7): warning C4100: unreferenced parameter\n"
    "foo_tests.obj : error LNK2019: unresolved external symbol \"int __cdecl baz(void)\" referenced in function main\n"
)
GOOD_HEADER = "#pragma once\ninline int foo() { return 1; }\n"
GOOD_TEST = '#include "x/foo.hpp"\nint main() { return foo() == 1 ? 0 : 1; }\n'


def reply(header: str = GOOD_HEADER, test: str = GOOD_TEST) -> str:
    return (f"=== FILE: src/simulation/include/x/foo.hpp ===\n```cpp\n{header}```\n"
            f"=== FILE: src/simulation/tests/foo_tests.cpp ===\n{test}=== END ===\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "src/simulation/include/x").mkdir(parents=True)
    (root / "src/simulation/include/x/existing.hpp").write_text("// existing\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for args in (("config", "user.email", "o@example.com"), ("config", "user.name", "Owner"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    return root


def task(root: Path, **over) -> cb.Task:
    values = dict(id="t1", objective="Add foo()", workspace=root, allowed_paths=["src/simulation/**"],
                  forbidden_paths=["src/simulation/secret/**"],
                  files=["src/simulation/include/x/foo.hpp", "src/simulation/tests/foo_tests.cpp"],
                  context_files=["src/simulation/include/x/existing.hpp"], build=["build"], test=["test"],
                  rules=["C++20, header only"])
    values.update(over)
    return cb.Task(**values)


class Script:
    """Scripted model replies and tool results; records every prompt and command."""

    def __init__(self, replies, builds=(), tests=()):
        self.replies, self.builds, self.tests = list(replies), list(builds), list(tests)
        self.prompts, self.commands = [], []

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)

    def run(self, cmd, cwd, timeout):
        self.commands.append(cmd)
        if cmd[0] == "build":
            return self.builds.pop(0)
        if cmd[0] == "test":
            return self.tests.pop(0)
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False).returncode, ""


def test_files_come_from_delimited_blocks_and_fences_are_unwrapped() -> None:
    files = cb.parse_files(reply())
    assert files == {"src/simulation/include/x/foo.hpp": GOOD_HEADER, "src/simulation/tests/foo_tests.cpp": GOOD_TEST}
    assert cb.parse_files("I think you want a header.") == {}


@pytest.mark.parametrize("path, expected", [
    ("src/simulation/include/x/a.hpp", []),
    ("unreal/Source/Everward/a.cpp", ["OUT_OF_SCOPE:unreal/Source/Everward/a.cpp"]),
    ("src/simulation/secret/key.hpp", ["OUT_OF_SCOPE:src/simulation/secret/key.hpp"]),
    ("../outside.hpp", ["PATH_NOT_RELATIVE:../outside.hpp"]),
    ("/etc/passwd", ["PATH_NOT_RELATIVE:/etc/passwd"]),
    ("C:\\x\\a.hpp", ["PATH_NOT_RELATIVE:C:\\x\\a.hpp"]),
])
def test_a_write_outside_the_task_scope_is_rejected_before_it_touches_disk(repo: Path, path: str, expected: list) -> None:
    assert cb.scope_problems([path], task(repo)) == expected


def test_compiler_errors_are_extracted_without_warnings_or_noise() -> None:
    found = cb.diagnostics(MSVC_ERROR + "Build started...\n", 25)
    assert found[0].startswith("foo.hpp(12): C2065: 'bar': undeclared identifier")
    assert any("LNK2019" in line for line in found) and not any("C4100" in line for line in found)
    assert cb.diagnostics("src/a.cpp:3:7: error: 'x' was not declared in this scope\n", 25) == [
        "a.cpp(3): error: 'x' was not declared in this scope"]
    assert len(cb.diagnostics("\n".join(f"f.cpp({i}): error C{i}: m{i}" for i in range(60)), 25)) == 25


def test_the_failure_fingerprint_ignores_paths_and_line_numbers_but_not_the_error() -> None:
    a = ["foo.hpp(12): C2065: 'bar': undeclared identifier"]
    b = ["foo.hpp(99): C2065: 'bar': undeclared identifier"]
    assert cb.fingerprint(a) == cb.fingerprint(b) != cb.fingerprint(["foo.hpp(12): C2065: 'baz': undeclared identifier"])


def test_a_change_that_builds_and_passes_is_success_with_a_diff_and_evidence(repo: Path) -> None:
    script = Script([reply()], builds=[(0, "ok")], tests=[(0, "100% tests passed")])
    result = cb.build_change(task(repo), script.ask, run=script.run)
    assert result["status"] == cb.SUCCESS and len(result["iterations"]) == 1 and result["iterations"][0]["stage"] == cb.SUCCESS
    assert (repo / "src/simulation/include/x/foo.hpp").read_text(encoding="utf-8") == GOOD_HEADER
    assert result["files"] == ["src/simulation/include/x/foo.hpp", "src/simulation/tests/foo_tests.cpp"]
    assert "inline int foo()" in result["diff"] and result["seconds"] >= 0
    assert "TASK: Add foo()" in script.prompts[0] and "// existing" in script.prompts[0] and "C++20, header only" in script.prompts[0]


def test_compiler_diagnostics_not_raw_logs_drive_the_repair_and_the_second_try_can_pass(repo: Path) -> None:
    script = Script([reply("#pragma once\ninline int foo() { return bar; }\n"), reply()],
                    builds=[(1, MSVC_ERROR), (0, "ok")], tests=[(0, "ok")])
    result = cb.build_change(task(repo), script.ask, run=script.run)
    assert result["status"] == cb.SUCCESS and [i["stage"] for i in result["iterations"]] == ["BUILD", cb.SUCCESS]
    repair = script.prompts[1]
    assert "ERRORS (compile)" in repair and "C2065: 'bar': undeclared identifier" in repair and "C4100" not in repair
    assert "CURRENT FILES" in repair and "return bar;" in repair


def test_a_test_failure_is_fed_back_and_the_same_failure_twice_ends_the_task(repo: Path) -> None:
    failing = (1, "Test #1: foo ...***Failed\nAssertion failed: foo() == 2")
    script = Script([reply(), reply(), reply()], builds=[(0, ""), (0, ""), (0, "")], tests=[failing, failing, failing])
    result = cb.build_change(task(repo), script.ask, run=script.run)
    assert result["status"] == "REPEATED_IDENTICAL_FAILURE" and len(result["iterations"]) == 2   # the third call never happens
    assert len(script.prompts) == 2 and "ERRORS (test)" in script.prompts[1]
    assert not (repo / "src/simulation/include/x/foo.hpp").exists()                              # rolled back
    assert result["rolled_back"] is True


def test_the_iteration_limit_bounds_the_work_and_failure_rolls_everything_back(repo: Path) -> None:
    def broken(n: int) -> str:
        return reply(f"#pragma once\ninline int foo() {{ return undefined{n}; }}\n")

    script = Script([broken(i) for i in range(5)],
                    builds=[(1, f"x.hpp({i}): error C2065: 'undefined{i}': undeclared identifier") for i in range(5)])
    result = cb.build_change(task(repo), script.ask, run=script.run, limits=cb.Limits(max_iterations=3))
    assert result["status"] == "ITERATIONS_EXHAUSTED" and len(script.prompts) == 3
    assert not (repo / "src/simulation/tests/foo_tests.cpp").exists() and result["rolled_back"] is True
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout == ""


def test_an_out_of_scope_write_is_never_applied_and_is_corrected_on_the_next_call(repo: Path) -> None:
    bad = reply() + "=== FILE: unreal/Source/Everward/Hack.cpp ===\nint x;\n=== END ===\n"
    script = Script([bad, reply()], builds=[(0, "")], tests=[(0, "")])
    result = cb.build_change(task(repo), script.ask, run=script.run)
    assert result["status"] == cb.SUCCESS and result["iterations"][0]["stage"] == "SCOPE"
    assert not (repo / "unreal").exists()
    assert "OUT_OF_SCOPE:unreal/Source/Everward/Hack.cpp" in script.prompts[1]


def test_unparseable_output_is_repaired_with_a_format_reminder(repo: Path) -> None:
    script = Script(["Sure! Here is how you could do it...", reply()], builds=[(0, "")], tests=[(0, "")])
    result = cb.build_change(task(repo), script.ask, run=script.run)
    assert result["status"] == cb.SUCCESS and result["iterations"][0]["stage"] == "PARSE"
    assert "no '=== FILE: path ===' blocks" in script.prompts[1]


def test_no_model_call_is_made_without_memory_and_waiting_spends_no_attempt(repo: Path) -> None:
    levels = iter([2.0, 2.5, 8.0])
    waited: list[float] = []
    script = Script([reply()], builds=[(0, "")], tests=[(0, "")])
    result = cb.build_change(task(repo), script.ask, run=script.run, free_gib=lambda: next(levels), sleep=waited.append)
    assert result["status"] == cb.SUCCESS and len(waited) == 2 and len(result["iterations"]) == 1
    starved = Script([])
    result = cb.build_change(task(repo), starved.ask, run=starved.run, free_gib=lambda: 1.0, sleep=lambda _s: None)
    assert result["status"] == "WAITING_FOR_MEMORY" and starved.prompts == [] and result["iterations"] == []


def test_a_failing_prelude_stops_before_any_model_call(repo: Path) -> None:
    script = Script([])
    result = cb.build_change(task(repo, prelude=[["configure"]]), script.ask,
                             run=lambda cmd, cwd, t: (2, "cmake: no compiler found"))
    assert result["status"] == "PRELUDE_FAILED" and "no compiler" in result["reason"] and script.prompts == []


def test_a_task_that_times_out_in_the_build_is_reported_as_a_failure_not_a_hang(repo: Path) -> None:
    code, out = cb.default_run(["python", "-c", "import time; time.sleep(30)"], repo, 1)
    assert code == 124 and "TIMEOUT" in out
    assert cb.default_run(["definitely-not-a-program-xyz"], repo, 5)[0] == 127


def test_configure_runs_after_every_write_and_before_the_build_and_its_failure_is_a_build_failure(repo: Path) -> None:
    order: list[str] = []

    def run(cmd, cwd, timeout):
        order.append(cmd[0])
        if cmd[0] == "configure" and order.count("configure") == 1:
            return 1, "CMake Error: Cannot find source file: tests/foo_tests.cpp"
        return 0, ""

    script = Script([reply(), reply()])
    result = cb.build_change(task(repo, configure=[["configure"]]), script.ask, run=run)
    assert result["status"] == cb.SUCCESS and order[:5] == ["configure", "configure", "build", "test", order[4]]
    assert result["iterations"][0]["stage"] == "BUILD" and "Cannot find source file" in script.prompts[1]


def test_context_can_be_a_line_range_so_the_prompt_carries_only_the_declarations_it_needs(repo: Path) -> None:
    big = repo / "src/simulation/include/x/big.hpp"
    big.write_text("\n".join(f"line {i}" for i in range(1, 101)), encoding="utf-8")
    prompt = cb.initial_prompt(task(repo, context_files=["src/simulation/include/x/big.hpp#L10-12"]), cb.Limits())
    assert "line 10\nline 11\nline 12" in prompt and "line 13" not in prompt
    assert "line 9\n" not in prompt
    assert cb._read(task(repo), "src/simulation/include/x/missing.hpp#L1-2", 100) == ""


def test_a_model_that_times_out_ends_the_task_with_evidence_and_a_clean_workspace(repo: Path) -> None:
    def slow(_prompt: str) -> str:
        raise TimeoutError("timed out after 600s")

    result = cb.build_change(task(repo), slow, run=lambda c, d, t: (0, ""))
    assert result["status"] == "MODEL_UNAVAILABLE" and "TimeoutError" in result["reason"] and result["iterations"][0]["stage"] == "MODEL"
    assert result["rolled_back"] is True


def test_the_memory_guard_aborts_only_on_sustained_pressure_and_remembers_the_lowest_reading() -> None:
    readings = iter([6.0, 0.3, 4.0, 0.3, 0.2, 0.1])
    guard = cb.MemoryGuard(lambda: next(readings), floor_gib=0.5, patience=2)
    assert guard.lowest == 6.0
    assert [guard.check() for _ in range(5)] == [False, False, False, True, True]   # a single dip is tolerated
    assert guard.lowest == 0.1 or guard.lowest == 0.2
