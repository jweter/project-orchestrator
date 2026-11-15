"""Guarded live-canary command refuses unsafe execution without local model calls."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import iron_god_live_ollama_canary as live

SHA = "a" * 40
SOURCES = (
    "def alpha() -> int:\n    return 1 + 1\n",
    "def beta() -> int:\n    return 10 - 5\n",
    "def gamma() -> int:\n    return 3 * 3\n",
)


def test_cli_does_not_request_real_model_without_explicit_flag(capsys) -> None:
    assert live.main([]) == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "EXPLICIT_LIVE_CANARY_REQUEST_REQUIRED"
    assert not receipt["live_ollama_proven"]
    assert not receipt["engine_started"]


def test_live_flag_without_accepted_release_still_refuses(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.delenv("IRON_GOD_ACCEPTED_SHA", raising=False)
    assert live.main(["--run-disposable-live"]) == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "ACCEPTED_RELEASE_NOT_IDENTIFIED"


def test_live_flag_with_wrong_checkout_sha_does_not_run_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", SHA)
    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(stdout="b" * 40),
    )
    from project_orchestrator import iron_god_local_model as local_model

    monkeypatch.setattr(
        local_model,
        "guarded_generate",
        lambda *_a, **_kw: pytest.fail("no model call for unpinned HEAD"),
    )
    assert live.main(["--run-disposable-live"]) == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "RELEASE_HEAD_MISMATCH"
    assert not receipt["live_ollama_proven"]


def test_true_owner_denial_refuses_even_if_sha_matches(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", SHA)
    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(stdout=SHA),
    )
    from project_orchestrator import execution_authority as owner
    from project_orchestrator import iron_god_local_model as local_model

    monkeypatch.setattr(
        owner,
        "evaluate_execution_authority",
        lambda **_kw: SimpleNamespace(permitted=False),
    )
    monkeypatch.setattr(
        local_model,
        "guarded_generate",
        lambda *_a, **_kw: pytest.fail("owner is paused; no Ollama"),
    )
    assert live.main(["--run-disposable-live"]) == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "OWNER_AUTHORITY_DENIED"


def test_injected_local_model_uses_real_three_commit_pipeline(
    tmp_path: Path,
) -> None:
    requested: list[str] = []

    def fake_local(prompt: str):
        requested.append(prompt)
        for i, name in enumerate(("alpha", "beta", "gamma")):
            if f"def {name}()" in prompt:
                return {"source": SOURCES[i]}
        raise AssertionError("unexpected codegen task")

    record = live.run_disposable(
        expected_release_sha=SHA,
        actual_release_sha=SHA,
        authorized=lambda: True,
        ask_local=fake_local,
    )
    assert record["status"] == "THREE_INJECTED_LOCAL_MICROEDITS_VERIFIED"
    assert record["verified_edits"] == 3
    assert record["local_model_requests"] == 3
    assert len(set(record["disposable_commit_shas"])) == 3
    safe = live._safe_receipt(record, actual_local_model=False)
    assert not safe["live_ollama_proven"]
    assert not safe["zero_cloud_token_use_proven"]
    assert not safe["deployment_ready"]
    assert len(requested) == 3


def test_revocation_after_model_causes_no_success_claim() -> None:
    owner = [True]

    def changed(_prompt: str):
        owner[0] = False
        return {"source": SOURCES[0]}

    record = live.run_disposable(
        expected_release_sha=SHA,
        actual_release_sha=SHA,
        authorized=lambda: owner[0],
        ask_local=changed,
    )
    assert record["status"] == "LOCAL_DISPOSABLE_CANARY_BLOCKED_OR_FAILED"
    assert record["verified_edits"] == 0
    assert record["local_model_requests"] == 1
    assert not live._safe_receipt(record, actual_local_model=True)["live_ollama_proven"]


def test_optimized_interpreter_cannot_claim_live_verified_edits() -> None:
    """Regression for Codex P2: -O strips legacy fixture assert gates."""
    command = [
        sys.executable,
        "-O",
        str(live.ROOT / "scripts" / "iron_god_live_ollama_canary.py"),
        "--run-disposable-live",
    ]
    proc = subprocess.run(
        command,
        cwd=live.ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 2
    receipt = json.loads(proc.stdout)
    assert receipt["status"] == "OPTIMIZED_PYTHON_UNSAFE_FOR_CANARY"
    assert receipt["verified_edits"] == 0
    assert receipt["live_ollama_proven"] is False
    assert receipt["local_ollama_requested"] is False
    assert receipt["engine_started"] is False


def test_ollama_telemetry_counts_only_proven_local_model_tokens() -> None:
    replies = [
        {
            "model_used": "qwen2.5-coder:7b",
            "prompt_eval_count": 55,
            "eval_count": 21,
            "lowest_available_gib": 4.2,
        },
        {
            "model_used": "qwen2.5-coder:7b",
            "prompt_eval_count": 62,
            "eval_count": 19,
            "lowest_available_gib": 3.9,
        },
        {
            "model_used": "qwen2.5-coder:7b",
            "prompt_eval_count": 69,
            "eval_count": 27,
            "lowest_available_gib": 4.4,
        },
    ]
    totals = live.summarize_ollama_telemetry(
        replies,
        expected_model="qwen2.5-coder:7b",
        elapsed_s=60,
        verified_edits=3,
    )
    assert totals["provenance_verified"] is True
    assert totals["local_prompt_tokens"] == 186
    assert totals["local_output_tokens"] == 67
    assert totals["lowest_available_gib"] == 3.9
    assert totals["verified_edits_per_hour"] == 180
    assert totals["cloud_token_calls_observed"] == 0
    assert totals["cloud_token_use_outside_this_process"] == "UNVERIFIED"


@pytest.mark.parametrize(
    "tamper",
    [
        {"model_used": "claude-code"},
        {"prompt_eval_count": None},
        {"eval_count": -1},
        {"lowest_available_gib": -0.1},
        {"prompt_eval_count": True},
    ],
)
def test_unverified_local_model_telemetry_does_not_claim_proof(tamper) -> None:
    value = {
        "model_used": "qwen2.5-coder:7b",
        "prompt_eval_count": 15,
        "eval_count": 6,
        "lowest_available_gib": 3.5,
    }
    value.update(tamper)
    totals = live.summarize_ollama_telemetry(
        [value],
        expected_model="qwen2.5-coder:7b",
        elapsed_s=30,
        verified_edits=3,
    )
    assert totals["provenance_verified"] is False
    assert totals["local_output_tokens"] is None
    assert totals["cloud_token_calls_observed"] is None
    assert totals["verified_edits_per_hour"] is None


def test_empty_telemetry_is_not_zero_cloud_proof() -> None:
    totals = live.summarize_ollama_telemetry(
        [],
        expected_model="qwen2.5-coder:7b",
        elapsed_s=0,
        verified_edits=0,
    )
    assert totals["provenance_verified"] is False
    assert totals["cloud_token_calls_observed"] is None
    assert totals["verified_edits_per_hour"] is None
