from scripts.evolution_activation_gate import validate_activation


def test_current_green_activation_is_allowed() -> None:
    state = {"enabled": True, "reason": "READINESS_PASS", "heads": {"repo": "abc"}}
    assert validate_activation(state, {"repo": "abc"}) == (True, "ACTIVATION_CURRENT")


def test_stale_head_is_rejected() -> None:
    state = {"enabled": True, "reason": "READINESS_PASS", "heads": {"repo": "abc"}}
    assert validate_activation(state, {"repo": "def"}) == (False, "ACTIVATION_HEADS_STALE")


def test_disabled_or_missing_evidence_is_rejected() -> None:
    assert validate_activation({"enabled": False, "heads": {"repo": "abc"}}, {"repo": "abc"})[0] is False
    assert validate_activation({"enabled": True, "reason": "READINESS_PASS", "heads": {}}, {})[0] is False
