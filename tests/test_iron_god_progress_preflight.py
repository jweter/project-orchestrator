"""The progress preflight runner records a result bound to one commit and never passes on doubt."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import iron_god_progress_preflight as runner


def result_of(tmp_path: Path, **kw) -> dict:
    kw.setdefault("head", lambda _tree: "a" * 40)
    return runner.run(tmp_path / "tree", tmp_path / "out" / "result.json", **kw)


def test_a_passing_preflight_is_recorded_for_the_exact_commit(tmp_path: Path) -> None:
    seen = {}

    def preflight(tree, check, timeout):
        seen.update(tree=tree, timeout=timeout)
        check()
        return {"ok": True, "returncode": 0, "output_tail": "28 passed"}

    result = result_of(tmp_path, preflight=preflight, authority=lambda: True)

    on_disk = json.loads((tmp_path / "out" / "result.json").read_text(encoding="utf-8"))
    assert result["ok"] is True and on_disk["ok"] is True and on_disk["head"] == "a" * 40
    assert seen["timeout"] == runner.PREFLIGHT_TIMEOUT_S and "finished_at" in on_disk


def test_a_failing_preflight_is_a_failed_result_with_its_evidence(tmp_path: Path) -> None:
    result = result_of(tmp_path, preflight=lambda *_a: {"ok": False, "returncode": 1, "output_tail": "ruff failed"},
                       authority=lambda: True)

    assert result["ok"] is False and result["output_tail"] == "ruff failed"


def test_a_revoked_execution_authority_never_runs_and_never_passes(tmp_path: Path) -> None:
    called = []

    def preflight(*_a):
        called.append(True)
        return {"ok": True}

    result = result_of(tmp_path, preflight=preflight, authority=lambda: False)

    assert result["ok"] is False and "EXECUTION_AUTHORITY_REVOKED" in result["output_tail"] and called == []


def test_authority_revoked_midway_fails_the_run(tmp_path: Path) -> None:
    state = {"n": 0}

    def authority() -> bool:
        state["n"] += 1
        return state["n"] < 2  # permitted at the start, revoked when the preflight re-checks

    def preflight(tree, check, timeout):
        check()
        return {"ok": True}

    assert result_of(tmp_path, preflight=preflight, authority=authority)["ok"] is False


def test_any_error_in_the_preflight_is_a_failed_result_not_a_crash(tmp_path: Path) -> None:
    def preflight(*_a):
        raise OSError("disk full")

    result = result_of(tmp_path, preflight=preflight, authority=lambda: True)

    assert result["ok"] is False and "OSError: disk full" in result["output_tail"]
    assert (tmp_path / "out" / "result.json").is_file()


def test_a_head_that_moves_during_verification_cannot_pass(tmp_path: Path) -> None:
    heads = iter(["a" * 40, "b" * 40])

    result = result_of(tmp_path, preflight=lambda *_a: {"ok": True, "returncode": 0}, authority=lambda: True,
                       head=lambda _tree: next(heads))

    assert result["ok"] is False and "HEAD MOVED" in result["output_tail"]
