from pathlib import Path

SCRIPT = Path("scripts/fallback/Watch-GitHubMailbox.ps1")


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def between(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def test_fallback_restart_checks_product_workers_before_stop():
    text = source()
    body = between(
        text,
        "function Invoke-SafeSupervisorRestart",
        "function Invoke-SafeMainDeploy",
    )
    assert "Get-ActiveProductWorkers" in body
    assert "DEFERRED_ACTIVE_PRODUCT_WORKERS" in body
    assert body.index("Get-ActiveProductWorkers") < body.index("Stop-Process")


def test_fallback_sync_checks_workers_before_actor_reset():
    text = source()
    body = between(
        text,
        "function Invoke-SafeActorSync",
        "function Invoke-SafeSupervisorRestart",
    )
    assert body.count("Get-ActiveProductWorkers") >= 2
    assert "REJECTED_ACTOR_REPO_DIRTY" in body
    assert body.index("Get-ActiveProductWorkers") < body.index("reset --hard origin/main")


def test_deferred_fallback_command_remains_retryable():
    text = source()
    assert "function Publish($o,[bool]$Consume=$true)" in text
    assert 'status="DEFERRED_ACTIVE_PRODUCT_WORKERS";consume=$false' in text
    assert "Publish $receipt ([bool]$result.consume)" in text


def test_atomic_safe_deploy_is_allowlisted():
    text = source()
    assert '"DEPLOY_MAIN_SAFE"' in text
    body = between(text, "function Invoke-SafeMainDeploy", "while($true)")
    assert "Invoke-SafeActorSync" in body
    assert "Invoke-SafeSupervisorRestart" in body
