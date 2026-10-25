"""Failure evidence must survive pytest's last-4000-character JSON excerpt."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import engineering.preflight as preflight


def test_full_failed_pytest_traceback_survives_beyond_json_tail(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "FAILURE_LOG_ROOT", tmp_path / "private-logs")
    trace = (
        "__________ test_failed_git_removal_preserves_worktree_and_branch __________\n"
        "E   AssertionError: deliberately injected failure evidence\n"
    )
    output = trace + "progress...\n" * 1200 + (
        "FAILED tests/test_iron_god_integration_worktree.py::"
        "test_failed_git_removal_preserves_worktree_and_branch\n"
    )

    def fake_run(_argv, **_kwargs):
        return subprocess.CompletedProcess(_argv, 1, output, "")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    result = preflight.run_check("tests", ["python", "-m", "pytest", "-q"])
    assert result["returncode"] == 1
    assert "deliberately injected" not in result["stdout"]  # truncated JSON
    artifact = result["failure_diagnostic"]
    assert artifact["stdout_omitted_chars"] == 0
    log_path = Path(artifact["path"])
    assert log_path.exists()
    full_log = log_path.read_text(encoding="utf-8")
    assert "deliberately injected failure evidence" in full_log
    assert "=== STDOUT ===" in full_log
    assert "=== STDERR ===" in full_log
    assert result["failed_test_nodes"] == [
        "tests/test_iron_god_integration_worktree.py::test_failed_git_removal_preserves_worktree_and_branch"
    ]
    assert not log_path.is_relative_to(preflight.ROOT)


def test_output_artifacts_are_local_unique_and_never_written_for_green(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "FAILURE_LOG_ROOT", tmp_path / "diagnostics")
    attempts = iter([1, 1, 0])

    def fake_run(argv, **_kwargs):
        status = next(attempts)
        return subprocess.CompletedProcess(argv, status, "trace", "")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    first = preflight.run_check("tests", ["test"])
    second = preflight.run_check("tests", ["test"])
    green = preflight.run_check("tests", ["test"])
    assert first["failure_diagnostic"]["path"] != second["failure_diagnostic"]["path"]
    assert Path(first["failure_diagnostic"]["path"]).exists()
    assert Path(second["failure_diagnostic"]["path"]).exists()
    assert green["failure_diagnostic"] is None
    assert green["returncode"] == 0
    assert len(list((tmp_path / "diagnostics").glob("*.log"))) == 2


def test_timeout_with_bytes_records_partial_trace_without_making_it_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "FAILURE_LOG_ROOT", tmp_path / "diagnostics")

    def expire(_argv, **kwargs):
        raise subprocess.TimeoutExpired(
            kwargs.get("args", _argv), kwargs["timeout"],
            output=b"EARLY WINDOWS TRACE\n" + b"." * 5000,
            stderr=b"partial stderr",
        )

    monkeypatch.setattr(preflight.subprocess, "run", expire)
    result = preflight.run_check("tests", ["python", "-m", "pytest"])
    assert result["returncode"] == 124
    assert result["timed_out"] is True
    assert "PREFLIGHT_CHECK_TIMEOUT:tests" in result["stderr"]
    path = Path(result["failure_diagnostic"]["path"])
    assert "EARLY WINDOWS TRACE" in path.read_text(encoding="utf-8")


def test_local_failure_evidence_is_bounded_with_explicit_loss_count(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "FAILURE_LOG_ROOT", tmp_path / "diagnostics")
    monkeypatch.setattr(preflight, "MAX_FAILURE_STREAM_CHARS", 1024)
    stdout = "EARLY_TRACE" + "x" * 5000 + "LATE_SUMMARY"
    result = preflight._write_failure_diagnostic("tests", stdout, "", returncode=1, timed_out=False)
    log = Path(result["path"]).read_text(encoding="utf-8")
    assert result["stdout_omitted_chars"] == len(stdout) - 1024
    assert f"DIAGNOSTIC_TRUNCATED:{len(stdout)-1024}_CHARS" in log
    assert "EARLY_TRACE" in log
    assert "LATE_SUMMARY" in log
    assert len(log) < 1800


def test_credential_like_values_are_redacted_in_local_log(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "FAILURE_LOG_ROOT", tmp_path / "diagnostics")
    output = (
        "password=super-private\n"
        "Authorization: Bearer this-is-a-token\n"
        "ghp_" + "A" * 25 + "\n"
        "sk-" + "B" * 25 + "\n"
        "FAILED tests/test_broken.py::test_secret\n"
    )
    monkeypatch.setattr(
        preflight.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, output, ""),
    )
    result = preflight.run_check("tests", ["pytest"])
    log = Path(result["failure_diagnostic"]["path"]).read_text(encoding="utf-8")
    assert "super-private" not in log
    assert "this-is-a-token" not in log
    assert "ghp_" not in log
    assert "sk-" not in log
    assert "REDACTED_SECRET" in log


def test_failed_artifact_write_cannot_turn_red_into_green(monkeypatch, tmp_path):
    path = tmp_path / "unwritable-file"
    path.write_text("file, not directory", encoding="utf-8")
    monkeypatch.setattr(preflight, "FAILURE_LOG_ROOT", path)
    monkeypatch.setattr(
        preflight.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 2, "real failure", ""),
    )
    result = preflight.run_check("tests", ["pytest"])
    assert result["returncode"] == 2
    assert result["failure_diagnostic"]["error"].startswith("DIAGNOSTIC_WRITE_FAILED:")


def test_main_records_pointer_without_publishing_trace_to_json(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "FAILURE_LOG_ROOT", tmp_path / "logs")
    evidence = tmp_path / "preflight-evidence.json"
    monkeypatch.setattr(preflight, "EVIDENCE", evidence)
    monkeypatch.setattr(preflight, "git_head", lambda _root: "c" * 40)
    monkeypatch.setattr(preflight, "checks", [("tests", ["pytest"])])

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 1, "E   AssertionError: full traceback is not embedded in JSON\n", "",
        )

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    assert preflight.main() == 1
    data = json.loads(evidence.read_text(encoding="utf-8"))
    assert data["status"] == "FAILED"
    assert data["head_sha"] == "c" * 40
    entry = data["results"][0]
    assert entry["returncode"] == 1
    assert Path(entry["failure_diagnostic"]["path"]).exists()
    assert "AssertionError" in Path(entry["failure_diagnostic"]["path"]).read_text(encoding="utf-8")


def test_real_child_failure_persists_untruncated_traceback(monkeypatch, tmp_path):
    import sys

    monkeypatch.setattr(preflight, "FAILURE_LOG_ROOT", tmp_path / "diagnostics")
    simulated_trace = "ROOT_CAUSE_AT_START_OF_PYTEST_OUTPUT\\n"
    code = (
        "import sys;"
        f"print({simulated_trace!r},flush=True);"
        "print('progress...' * 1200,flush=True);"
        "sys.exit(7)"
    )
    result = preflight.run_check("tests", [sys.executable, "-c", code])
    assert result["returncode"] == 7
    assert result["timed_out"] is False
    assert "ROOT_CAUSE_AT_START" not in result["stdout"]
    assert "ROOT_CAUSE_AT_START" in Path(result["failure_diagnostic"]["path"]).read_text(
        encoding="utf-8"
    )
