"""CLI receipts cannot misrepresent disposable test coverage as live Ollama proof."""

from __future__ import annotations

from types import SimpleNamespace

from scripts import iron_god_three_microedit_canary as canary


def test_success_is_explicitly_not_a_live_cloud_free_autonomy_proof(
    monkeypatch,
) -> None:
    seen = []

    def fake(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="1 passed in 3.15s\n", stderr="")

    monkeypatch.setattr(canary.subprocess, "run", fake)
    code, receipt = canary.run()
    assert code == 0
    assert receipt["status"] == "DISPOSABLE_THREE_MICROEDITS_AND_RESTART_PASSED"
    assert not receipt["engine_started"]
    assert not receipt["live_ollama_proven"]
    assert not receipt["zero_cloud_token_use_proven"]
    assert not receipt["product_reality_proven"]
    assert len(seen) == 1
    assert seen[0][0][0:3] == [canary.sys.executable, "-m", "pytest"]


def test_failures_refuse_to_claim_the_canary_passed(monkeypatch) -> None:
    monkeypatch.setattr(
        canary.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(
            returncode=1, stdout="1 failed in 0.20s\n", stderr=""
        ),
    )
    code, receipt = canary.run()
    assert code == 1
    assert receipt["status"] == "DISPOSABLE_THREE_MICROEDIT_TEST_FAILED"
    assert not receipt["live_ollama_proven"]


def test_runner_unavailable_fails_closed(monkeypatch) -> None:
    def broken(*_a, **_kw):
        raise OSError("missing local pytest")

    monkeypatch.setattr(canary.subprocess, "run", broken)
    code, receipt = canary.run()
    assert code == 2
    assert receipt["status"] == "DISPOSABLE_GIT_SQLITE_TEST_UNAVAILABLE"
    assert receipt["error_type"] == "OSError"
    assert not receipt["engine_started"]
