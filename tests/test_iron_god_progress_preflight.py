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


def test_a_long_failing_output_keeps_the_first_failure_not_only_the_summary_tail() -> None:
    early = "mypy: src/pkg/mod.py:12: error: Incompatible return value type"
    long_output = "collected 8700 items\n" + early + "\n" + ("noise line\n" * 600) + "Found 1 error in 1 file"

    kept = runner.evidence_of(long_output)

    assert early in kept
    assert kept.endswith("Found 1 error in 1 file")
    assert "characters omitted" in kept and len(kept) <= runner.EVIDENCE_LIMIT + 60


def test_short_output_is_kept_whole_and_output_without_a_failure_marker_keeps_its_tail() -> None:
    assert runner.evidence_of("28 passed") == "28 passed"
    plain = "line\n" * 1000 + "all done"
    kept = runner.evidence_of(plain)
    assert kept == plain[-runner.EVIDENCE_LIMIT:] and kept.endswith("all done")


def test_the_recorded_result_uses_the_failure_digest(tmp_path: Path) -> None:
    early = "ruff: src/a.py:3: error: unused import"
    noisy = early + "\n" + "x\n" * 2000 + "1 failed"
    result = result_of(tmp_path, preflight=lambda *_a: {"ok": False, "returncode": 1, "output_tail": noisy},
                       authority=lambda: True)

    assert early in result["output_tail"] and result["output_tail"].endswith("1 failed")


def test_an_uppercase_failed_marker_without_a_traceback_is_found() -> None:
    early = "FAILED tests/test_worker_identity.py::test_corrupt_key - assert False"
    kept = runner.evidence_of(early + "\n" + "ok line\n" * 800 + "1 failed, 8699 passed")

    assert kept.startswith(early[:40]) or early in kept
    assert kept.endswith("1 failed, 8699 passed")
