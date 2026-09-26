import json
import subprocess

import scripts.autonomous_slice_builder as builder


def result(stderr="", stdout="", returncode=1):
    return subprocess.CompletedProcess(["runner"], returncode, stdout=stdout, stderr=stderr)


def test_session_limit_is_runner_unavailable():
    reason=builder.runner_unavailable_reason(
        "claude-code", result(stderr="Session limit reached · resets 9:10pm")
    )
    assert reason=="claude-code:SESSION_LIMIT"


def test_quota_and_rate_limits_are_runner_unavailable():
    assert builder.runner_unavailable_reason(
        "claude-code",result(stderr="quota exceeded")
    )=="claude-code:QUOTA_EXCEEDED"
    assert builder.runner_unavailable_reason(
        "aider-ollama",result(stdout="429 Too Many Requests")
    )=="aider-ollama:RATE_LIMIT"


def test_code_failure_is_not_runner_unavailable():
    assert builder.runner_unavailable_reason(
        "claude-code",result(stderr="pytest failed: assertion error")
    ) is None


def test_runner_unavailable_releases_grant_as_deferred(tmp_path):
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    grant_dir=tmp_path/"hands-off-execution-grants"
    grant_dir.mkdir()
    grant=grant_dir/"g.json"
    grant.write_text(json.dumps({"grant_id":"g"}),encoding="utf-8")
    lifecycle.write_text(json.dumps({"grants":{"g":{"state":"CLAIMED"}}}),encoding="utf-8")
    builder.finish_grant_lifecycle(
        grant,{"status":"RUNNER_UNAVAILABLE","repository":"jweter/repo"}
    )
    record=json.loads(lifecycle.read_text(encoding="utf-8"))["grants"]["g"]
    assert record["state"]=="DEFERRED"
    assert record["result_status"]=="RUNNER_UNAVAILABLE"
