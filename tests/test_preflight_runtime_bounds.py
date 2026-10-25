import json
import subprocess

import engineering.preflight as preflight


def test_tests_get_longer_but_bounded_timeout():
    assert preflight.timeout_for_check("tests") == preflight.TEST_CHECK_TIMEOUT_SECONDS
    assert preflight.timeout_for_check("controller_proofs") == preflight.CONTROLLER_PROOFS_TIMEOUT_SECONDS
    assert preflight.timeout_for_check("control_plane") == preflight.DEFAULT_CHECK_TIMEOUT_SECONDS
    assert preflight.timeout_for_check("resident_dispatch_proof") == preflight.DEFAULT_CHECK_TIMEOUT_SECONDS
    assert preflight.DEFAULT_CHECK_TIMEOUT_SECONDS < preflight.CONTROLLER_PROOFS_TIMEOUT_SECONDS < preflight.TEST_CHECK_TIMEOUT_SECONDS


def test_run_check_classifies_timeout_fail_closed(monkeypatch):
    def expire(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=kwargs.get("args", args[0] if args else ["x"]),
            timeout=kwargs["timeout"],
            output="partial-out",
            stderr="partial-err",
        )

    monkeypatch.setattr(preflight.subprocess, "run", expire)
    result = preflight.run_check("control_plane", ["python", "validator.py"])
    assert result["returncode"] == 124
    assert result["timed_out"] is True
    assert result["timeout_seconds"] == preflight.DEFAULT_CHECK_TIMEOUT_SECONDS
    assert "PREFLIGHT_CHECK_TIMEOUT:control_plane" in result["stderr"]


def test_incremental_evidence_records_active_check(tmp_path, monkeypatch):
    evidence = tmp_path / "preflight-evidence.json"
    monkeypatch.setattr(preflight, "EVIDENCE", evidence)
    monkeypatch.setattr(preflight, "git_head", lambda root: "abc123")

    preflight.write_evidence(
        status="RUNNING",
        results=[{"name": "control_plane", "returncode": 0}],
        active_check="tests",
        started_at="2026-09-26T00:00:00+00:00",
    )

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 3
    assert payload["status"] == "RUNNING"
    assert payload["active_check"] == "tests"
    assert payload["head_sha"] == "abc123"
    assert payload["results"][0]["name"] == "control_plane"


def test_run_check_classifies_keyboard_interrupt_as_interrupted(monkeypatch):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(preflight.subprocess, "run", interrupt)
    result = preflight.run_check("tests", ["python", "-m", "pytest", "-q"])

    assert result["returncode"] == 130
    assert result["interrupted"] is True
    assert result["timed_out"] is False
    assert result["stderr"] == "PREFLIGHT_CHECK_INTERRUPTED:tests"


def test_main_finalizes_interrupted_evidence(tmp_path, monkeypatch):
    evidence = tmp_path / "preflight-evidence.json"
    monkeypatch.setattr(preflight, "EVIDENCE", evidence)
    monkeypatch.setattr(preflight, "checks", [("tests", ["pytest"])])
    monkeypatch.setattr(preflight, "git_head", lambda root: "deadbeef")
    monkeypatch.setattr(
        preflight,
        "run_check",
        lambda name, argv: {
            "name": name,
            "argv": argv,
            "returncode": 130,
            "stdout": "",
            "stderr": "PREFLIGHT_CHECK_INTERRUPTED:tests",
            "timed_out": False,
            "interrupted": True,
            "timeout_seconds": 900,
            "duration_seconds": 1.0,
        },
    )

    assert preflight.main() == 130
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["status"] == "INTERRUPTED"
    assert payload["active_check"] is None
    assert payload["head_sha"] == "deadbeef"
    assert payload["results"][0]["interrupted"] is True


def test_main_fails_closed_when_head_moves_during_check(tmp_path, monkeypatch):
    evidence = tmp_path / "preflight-evidence.json"
    monkeypatch.setattr(preflight, "EVIDENCE", evidence)
    monkeypatch.setattr(preflight, "checks", [("tests", ["pytest"])])

    heads = iter(["a" * 40, "a" * 40, "b" * 40])
    monkeypatch.setattr(preflight, "git_head", lambda _root: next(heads))
    monkeypatch.setattr(
        preflight,
        "run_check",
        lambda name, argv: {
            "name": name,
            "argv": argv,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "interrupted": False,
            "timeout_seconds": 900,
            "duration_seconds": 1.0,
        },
    )

    assert preflight.main() == 1
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["status"] == "FAILED"
    assert payload["head_sha"] == "a" * 40
    assert payload["results"][-1]["name"] == "exact_head_fence"
    assert payload["results"][-1]["expected_head_sha"] == "a" * 40
    assert payload["results"][-1]["observed_head_sha"] == "b" * 40


def test_write_evidence_can_pin_starting_head(tmp_path, monkeypatch):
    evidence = tmp_path / "preflight-evidence.json"
    monkeypatch.setattr(preflight, "EVIDENCE", evidence)
    monkeypatch.setattr(preflight, "git_head", lambda _root: "newer-head")

    preflight.write_evidence(
        status="RUNNING",
        results=[],
        active_check="tests",
        started_at="2026-09-26T00:00:00+00:00",
        head_sha="pinned-head",
    )

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["head_sha"] == "pinned-head"
