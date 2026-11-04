from __future__ import annotations

import json
from pathlib import Path

from project_orchestrator import iron_god_preflight_baseline as baseline

PYTEST = ["uv", "run", "--no-sync", "pytest", "tests/", "-q", "--tb=short"]
CHECKS = [
    {"id": "control_plane", "argv": ["python", "engineering/preflight.py", "--self-check"]},
    {"id": "backend_tests", "argv": PYTEST},
    {"id": "isolated_tests", "argv": ["uv", "run", "--no-sync", "pytest", "backend/tests/", "-q"]},
    {"id": "electron_contract", "argv": ["bun", "run", "check:electron"]},
]


def make_repo(tmp_path: Path, failed: str = "backend_tests", ran: tuple[str, ...] = ("control_plane", "backend_tests")) -> Path:
    (tmp_path / "engineering").mkdir()
    (tmp_path / "engineering" / "control-plane.json").write_text(json.dumps({"preflight": {"checks": CHECKS}}), encoding="utf-8")
    results = [{"id": i, "returncode": 1 if i == failed else 0} for i in ran]
    (tmp_path / "preflight-evidence.json").write_text(json.dumps({"results": results}), encoding="utf-8")
    return tmp_path


def failures(*ids: str) -> str:
    return "\n".join(f"FAILED {i} - AssertionError" for i in ids) + f"\n{len(ids)} failed\n"


class Fake:
    """post = failures of the change's tree; base = failures of the untouched base; later = results of later checks."""

    def __init__(self, tmp_path: Path, post: tuple[str, ...], base: tuple[str, ...], later: dict[str, int] | None = None):
        self.base_dir = tmp_path / "base"
        self.post, self.base_failures, self.later, self.calls = post, base, later or {}, []

    def __call__(self, argv: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
        self.calls.append((argv, cwd))
        if argv[:4] == PYTEST[:4] and argv[4] not in ("tests/", "backend/tests/"):
            ids = set(argv[4:argv.index("-q")])
            return (1, failures(*sorted(ids & set(self.base_failures))))
        if argv == PYTEST:
            return (1, failures(*self.post)) if self.post else (0, "ok")
        for check in CHECKS:
            if argv == check["argv"]:
                return (self.later.get(check["id"], 0), "")
        raise AssertionError(argv)


def run(tmp_path: Path, fake: Fake, clean: bool = True) -> dict:
    return baseline.continue_preflight(tmp_path, fake.base_dir, fake, clean=lambda: clean)


def test_failures_that_the_base_already_has_pass_and_later_checks_still_run(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    fake = Fake(tmp_path, post=("tests/a.py::t1", "tests/b.py::t2"), base=("tests/a.py::t1", "tests/b.py::t2", "tests/c.py::t3"))
    result = run(repo, fake)
    assert result["ok"] and result["verdict"] == "BASELINE_MATCH"
    assert [c["id"] for c in result["checks"]] == ["backend_tests", "isolated_tests", "electron_contract"]
    assert any(argv == ["bun", "run", "check:electron"] for argv, _ in fake.calls)  # not skipped


def test_a_new_failure_that_the_base_does_not_have_fails(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    fake = Fake(tmp_path, post=("tests/a.py::t1", "tests/new.py::mine"), base=("tests/a.py::t1",))
    result = run(repo, fake)
    assert not result["ok"] and result["checks"][0]["new_failures"] == ["tests/new.py::mine"]


def test_a_later_check_that_fails_fails_the_preflight(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    fake = Fake(tmp_path, post=("tests/a.py::t1",), base=("tests/a.py::t1",), later={"electron_contract": 1})
    result = run(repo, fake)
    assert not result["ok"] and result["checks"][-1]["id"] == "electron_contract"


def test_a_failed_check_that_is_not_pytest_is_never_excused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, failed="electron_contract", ran=("control_plane", "backend_tests", "isolated_tests", "electron_contract"))
    assert run(repo, Fake(tmp_path, (), ()))["verdict"] == "FAILED_CHECK_IS_NOT_PYTEST"


def test_control_plane_and_fence_failures_are_never_excused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, failed="control_plane", ran=("control_plane",))
    assert run(repo, Fake(tmp_path, (), ()))["verdict"] == "UNEXCUSABLE_FAILURE"


def test_unreadable_inputs_fail_closed(tmp_path: Path) -> None:
    assert run(tmp_path, Fake(tmp_path, (), ()))["verdict"] == "BASELINE_INPUTS_UNREADABLE"


def test_unparseable_or_excessive_failures_fail_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    class Silent(Fake):
        def __call__(self, argv, cwd, timeout):  # type: ignore[no-untyped-def]
            return (1, "collection exploded, no summary lines")

    assert run(repo, Silent(tmp_path, (), ()))["checks"][0]["baseline"] == "NO_FAILING_IDS_PARSED"
    many = tuple(f"tests/x.py::t{i}" for i in range(baseline.MAX_IDS + 1))
    assert run(repo, Fake(tmp_path, many, many))["checks"][0]["baseline"] == "TOO_MANY_FAILURES"


def test_a_worktree_changed_by_a_check_fails(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    result = run(repo, Fake(tmp_path, ("tests/a.py::t1",), ("tests/a.py::t1",)), clean=False)
    assert not result["ok"] and result["verdict"] == "WORKTREE_CHANGED_DURING_VERIFICATION"


def test_base_run_only_receives_the_failing_ids() -> None:
    argv = baseline.ids_argv(PYTEST, ["tests/b.py::t2", "tests/a.py::t1"])
    assert argv[:4] == PYTEST[:4] and argv[4:6] == ["tests/a.py::t1", "tests/b.py::t2"] and "tests/" not in argv


def test_collection_errors_count_as_failing_ids() -> None:
    assert baseline.failing_ids("ERROR tests/x.py - ImportError\nFAILED tests/y.py::t - boom\n") == {"tests/x.py", "tests/y.py::t"}
