from scripts.blocker_buddy import diagnose


def test_capability_graph_blocker_requests_direction_without_inventing_it():
    result = diagnose(
        {"status": "NO_READY_EXECUTION_SLICE", "reason": "CAPABILITY_GRAPH_REQUIRED:jweter/example"},
        {"status": "ELIGIBLE_IDLE"},
    )
    assert result["status"] == "DIRECTION_REQUIRED"
    assert "never invent product direction" in result["action"]


def test_user_active_is_wait_not_failure():
    result = diagnose({"status": "USER_ACTIVE"}, {"status": "ELIGIBLE_IDLE"})
    assert result["status"] == "WAITING_POLITELY"
    assert "do not compete with the user" in result["action"]


def test_machine_failure_routes_to_bounded_repair():
    result = diagnose({"status": "BUILDER_FAILED"}, {"status": "STALE_ACTIVE_WORK"})
    assert result["status"] == "REPAIR_REQUIRED"
    assert "independently verify" in result["action"]


def test_eligible_idle_is_liveness_defect():
    result = diagnose({"status": "NO_OP"}, {"status": "ELIGIBLE_IDLE"})
    assert result["status"] == "LIVENESS_DEFECT"
