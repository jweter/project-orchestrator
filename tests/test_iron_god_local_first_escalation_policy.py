"""Regression: optional Claude capacity must not drain the primary local queue."""

from project_orchestrator.iron_god_product_lane import local_escalation_authorized


def test_enabled_claude_is_not_automatic_escalation_permission() -> None:
    assert not local_escalation_authorized({"enabled": True}, draft_locally=True)
    assert not local_escalation_authorized({"enabled": True, "allow_local_escalation": False}, draft_locally=True)


def test_explicit_escalation_requires_local_drafting_and_no_proving_mode() -> None:
    config = {"enabled": True, "allow_local_escalation": True}
    assert local_escalation_authorized(config, draft_locally=True)
    assert not local_escalation_authorized(config, draft_locally=False)
    assert not local_escalation_authorized({**config, "local_only_proving_mode": True}, draft_locally=True)
    assert not local_escalation_authorized({**config, "enabled": False}, draft_locally=True)


def test_missing_authorization_is_fail_closed() -> None:
    assert not local_escalation_authorized({}, draft_locally=True)
    assert not local_escalation_authorized({"enabled": True, "allow_local_escalation": None}, draft_locally=True)


def test_runtime_config_forwards_explicit_escalation_to_refill():
    """Guard the actual product dispatch wiring, not just the policy helper."""
    import inspect
    from project_orchestrator import iron_god_product_lane as lane

    source = inspect.getsource(lane)
    assert '"allow_local_escalation": config.get("allow_local_escalation") is True' in source
    assert local_escalation_authorized({"enabled": True, "allow_local_escalation": True}, draft_locally=True)
    assert not local_escalation_authorized({"enabled": True, "allow_local_escalation": False}, draft_locally=True)
