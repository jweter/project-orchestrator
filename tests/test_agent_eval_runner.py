from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from project_orchestrator.agent_eval_runner import (
    AgentEvalRunnerError,
    run_matrix,
    validate_runner_config,
)
from project_orchestrator.kpi_telemetry import load_events


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Agent Eval Test")
    (root / "guard.txt").write_text("good\n", encoding="utf-8")
    (root / "other.txt").write_text("old\n", encoding="utf-8")
    git(root, "add", "guard.txt", "other.txt")
    git(root, "commit", "-m", "baseline")
    return root, git(root, "rev-parse", "HEAD")


def suite(baseline: str, fixture: str = "fixture.patch") -> dict:
    return {
        "schema_version": 1,
        "suite_id": "runner-test-v1",
        "purpose": "runner regression test",
        "advisory_only": True,
        "product_reality_may_be_inferred": False,
        "canonical_kpi_ledger": "engineering/kpi-events.jsonl",
        "tasks": [
            {
                "task_id": "restore-guard",
                "title": "Restore guard",
                "repository": "jweter/project-orchestrator",
                "acceptance_kind": "DETERMINISTIC",
                "prompt": "Restore guard.txt to the exact text good.",
                "baseline_ref": baseline,
                "fault_fixture_ref": fixture,
                "measurement_contract_ref": "guard.txt == good",
                "verification_commands": [
                    "python -c \"from pathlib import Path; raise SystemExit(0 if Path('guard.txt').read_text().strip() == 'good' else 1)\""
                ],
                "required_stages": ["PREFLIGHT"],
            }
        ],
    }


def runner_config(script: str, config_id: str = "repair") -> dict:
    return {
        "schema_version": 1,
        "configurations": [
            {
                "agent_id": "fake-python-agent",
                "agent_config_id": config_id,
                "argv": [sys.executable, "-c", script],
                "timeout_seconds": 30,
            }
        ],
    }


def test_runner_uses_disposable_exact_baseline_and_records_kpi_events(tmp_path: Path, monkeypatch):
    root, baseline = make_repo(tmp_path)
    (root / "fixture.patch").write_text(
        "diff --git a/guard.txt b/guard.txt\n"
        "index 12799cc..67be85f 100644\n"
        "--- a/guard.txt\n"
        "+++ b/guard.txt\n"
        "@@ -1 +1 @@\n"
        "-good\n"
        "+bad\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    ledger = tmp_path / "events.jsonl"
    output = tmp_path / "artifacts"

    result = run_matrix(
        suite(baseline),
        runner_config("from pathlib import Path; Path('guard.txt').write_text('good\\n')"),
        repo_roots={"jweter/project-orchestrator": root},
        output_dir=output,
        ledger=ledger,
        matrix_id="matrix-pass",
        record=True,
    )

    assert result["attempts"] == 1
    assert result["pass_count"] == 1
    assert result["fail_count"] == 0
    assert result["invalid_benchmarks"] == 0
    assert (root / "guard.txt").read_text(encoding="utf-8") == "good\n"
    assert git(root, "rev-parse", "HEAD") == baseline
    events = load_events(ledger)
    assert events
    assert all(event["project"] == "portfolio" for event in events)
    assert all(event.get("metadata", {}).get("agent_eval") for event in events)
    attempt = json.loads((output / "matrix-pass" / "repair" / "restore-guard" / "attempt.json").read_text())
    assert attempt["acceptance"]["outcome"] == "PASS"


def test_runner_records_deterministic_failure_when_agent_does_not_repair(tmp_path: Path, monkeypatch):
    root, baseline = make_repo(tmp_path)
    (root / "fixture.patch").write_text(
        "diff --git a/guard.txt b/guard.txt\n"
        "index 12799cc..67be85f 100644\n"
        "--- a/guard.txt\n"
        "+++ b/guard.txt\n"
        "@@ -1 +1 @@\n"
        "-good\n"
        "+bad\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)

    result = run_matrix(
        suite(baseline),
        runner_config("print('no repair')", config_id="no-repair"),
        repo_roots={"jweter/project-orchestrator": root},
        output_dir=tmp_path / "artifacts",
        ledger=tmp_path / "events.jsonl",
        matrix_id="matrix-fail",
        record=False,
    )

    assert result["pass_count"] == 0
    assert result["fail_count"] == 1
    assert result["results"][0]["acceptance"] == "FAIL"


def test_fault_fixture_must_prove_red_before_agent_runs(tmp_path: Path, monkeypatch):
    root, baseline = make_repo(tmp_path)
    (root / "fixture.patch").write_text(
        "diff --git a/other.txt b/other.txt\n"
        "index 3367afd..67be85f 100644\n"
        "--- a/other.txt\n"
        "+++ b/other.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+bad\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)

    result = run_matrix(
        suite(baseline),
        runner_config("raise SystemExit('agent should not run')", config_id="blocked"),
        repo_roots={"jweter/project-orchestrator": root},
        output_dir=tmp_path / "artifacts",
        ledger=tmp_path / "events.jsonl",
        matrix_id="matrix-invalid",
        record=False,
    )

    assert result["invalid_benchmarks"] == 1
    assert result["completed_attempts"] == 0
    assert result["results"][0]["status"] == "INVALID_BENCHMARK"


def test_runner_config_rejects_shell_wrappers():
    with pytest.raises(AgentEvalRunnerError, match="shell executables are forbidden"):
        validate_runner_config(
            {
                "schema_version": 1,
                "configurations": [
                    {
                        "agent_id": "bad",
                        "agent_config_id": "shell",
                        "argv": ["bash", "-c", "echo nope"],
                        "timeout_seconds": 30,
                    }
                ],
            }
        )


def test_runner_config_requires_bounded_timeout():
    with pytest.raises(AgentEvalRunnerError, match="timeout_seconds"):
        validate_runner_config(
            {
                "schema_version": 1,
                "configurations": [
                    {
                        "agent_id": "bad",
                        "agent_config_id": "timeout",
                        "argv": [sys.executable, "-c", "pass"],
                        "timeout_seconds": 0,
                    }
                ],
            }
        )
