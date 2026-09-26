from pathlib import Path

SCRIPT = Path("scripts/fallback/Watch-GitHubMailbox.ps1")


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def function_body(text: str, name: str, next_name: str) -> str:
    start = text.index(f"function {name}")
    end = text.index(f"function {next_name}", start)
    return text[start:end]


def test_fallback_restart_checks_product_workers_before_stop():
    text = source()
    body = function_body(text, "Invoke-SafeSupervisorRestart", "Invoke-SafeMainDeploy")
    assert "Get-ActiveProductWorkers" in body
    assert "DEFERRED_ACTIVE_PRODUCT_WORKERS" in body
    assert body.index("Get-ActiveProductWorkers") < body.index("Stop-Process")


def test_fallback_sync_checks_workers_before_actor_reset():
    text = source()
    body = function_body(text, "Invoke-SafeActorSync", "Invoke-SafeSupervisorRestart")
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
    body = function_body(text, "Invoke-SafeMainDeploy", "while")
    assert "Invoke-SafeActorSync" in body
    assert "Invoke-SafeSupervisorRestart" in body
