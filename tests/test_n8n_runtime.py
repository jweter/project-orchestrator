from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAILURES = ["failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"]


def run_classifier(filename: str, payload: dict) -> list[dict]:
    workflow = json.loads((ROOT / "n8n" / "workflows" / filename).read_text(encoding="utf-8"))
    classifier = next(node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.code")
    node = shutil.which("node")
    assert node is not None, "Node.js is required to test the committed n8n classifiers"
    result = subprocess.run(
        [
            node,
            "-e",
            "const {code, payload} = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            "process.stdout.write(JSON.stringify(new Function('$json', code)(payload)));",
        ],
        input=json.dumps({"code": classifier["parameters"]["jsCode"], "payload": payload}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("wrapped", [True, False])
@pytest.mark.parametrize("conclusion", FAILURES)
def test_committed_ci_classifier_reads_github_trigger_item(conclusion: str, wrapped: bool) -> None:
    payload = {
        "action": "completed",
        "repository": {"full_name": "jweter/knowledge-engine-core"},
        "check_suite": {
            "id": 123,
            "head_sha": "a" * 40,
            "conclusion": conclusion,
            "pull_requests": [{"number": 42}],
        },
    }
    # n8n 2.38.2 GithubTrigger.webhook emits body/headers/query; also accept direct items.
    item = {"body": payload, "headers": {}, "query": {}} if wrapped else payload
    assert run_classifier("ci-failure-router.json", item) == [
        {
            "json": {
                "repository": "jweter/knowledge-engine-core",
                "pr_number": 42,
                "head_sha": "a" * 40,
                "conclusion": conclusion,
                "check_suite_id": 123,
                "reason": "ci_failure",
            }
        }
    ]
    payload["check_suite"]["conclusion"] = None
    assert run_classifier("ci-failure-router.json", item) == []


@pytest.mark.parametrize("wrapped", [True, False])
def test_committed_impact_classifier_reads_github_trigger_item(wrapped: bool) -> None:
    payload = {
        "repository": {"full_name": "jweter/knowledge-engine-ai"},
        "ref": "refs/heads/main",
        "after": "b" * 40,
        "deleted": False,
    }
    item = {"body": payload, "headers": {}, "query": {}} if wrapped else payload
    assert run_classifier("knowledge-engine-impact-router.json", item) == [
        {
            "json": {
                "repository": "jweter/knowledge-engine-ai",
                "pr_number": "",
                "head_sha": "b" * 40,
                "conclusion": "",
                "check_suite_id": "",
                "reason": "knowledge_engine_main_change",
            }
        }
    ]
    payload["deleted"] = True
    assert run_classifier("knowledge-engine-impact-router.json", item) == []


def capture_event(output: Path, reason: str, conclusion: str | None) -> subprocess.CompletedProcess:
    argv = [
        sys.executable,
        str(ROOT / "engineering" / "capture_n8n_event.py"),
        "--source-repository", "jweter/knowledge-engine-core",
        "--source-head-sha", "a" * 40,
        "--reason", reason,
        "--output", str(output),
    ]
    if reason == "ci_failure":
        argv.extend(["--source-pr", "42"])
    if conclusion is not None:
        argv.extend(["--source-conclusion", conclusion])
    return subprocess.run(argv, cwd=ROOT, text=True, capture_output=True)


@pytest.mark.parametrize("conclusion", [None, "", "success", "neutral", "skipped", "unknown"])
def test_intake_rejects_ci_failure_without_terminal_failure(tmp_path: Path, conclusion: str | None) -> None:
    output = tmp_path / "evidence.json"
    result = capture_event(output, "ci_failure", conclusion)
    assert result.returncode != 0
    assert "conclusion" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("conclusion", FAILURES)
def test_intake_accepts_terminal_ci_failure(tmp_path: Path, conclusion: str) -> None:
    output = tmp_path / "evidence.json"
    result = capture_event(output, "ci_failure", conclusion)
    assert result.returncode == 0, result.stderr
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["state"] == "REPAIR_CANDIDATE"
    assert evidence["conclusion"] == conclusion


def test_intake_keeps_empty_conclusion_for_main_change(tmp_path: Path) -> None:
    output = tmp_path / "evidence.json"
    result = capture_event(output, "knowledge_engine_main_change", None)
    assert result.returncode == 0, result.stderr
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["state"] == "DEPENDENCY_RECHECK_CANDIDATE"
    assert evidence["conclusion"] is None
