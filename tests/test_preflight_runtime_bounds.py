import json
import subprocess

import engineering.preflight as preflight


def test_tests_get_longer_but_bounded_timeout():
    assert preflight.timeout_for_check("tests") == preflight.TEST_CHECK_TIMEOUT_SECONDS
    assert preflight.timeout_for_check("control_plane") == preflight.DEFAULT_CHECK_TIMEOUT_SECONDS
    assert preflight.TEST_CHECK_TIMEOUT_SECONDS > preflight.DEFAULT_CHECK_TIMEOUT_SECONDS


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
