"""Recovery ladder must never invent approval, acceptance or cloud authority."""
from project_orchestrator.iron_god_stall_recovery import recovery_decision as decide


TASK = {"id": "A", "route": {"builder": "LOCAL"}, "capability_level": 1}
LARGE = {"id": "A", "route": {"builder": "CAPABLE"}, "capability_level": 3}
ALT = [{"id": "B", "eligible": True, "owner_approved": True}]


def check(task=TASK, *, reason="TEST_FAILURE", attempts=2, alternatives=ALT,
          authorized=True, memory=True):
    return decide(task, reason=reason, attempts=attempts,
                  eligible_alternatives=alternatives,
                  owner_authorized=authorized, memory_available=memory)


def test_owner_pause_and_memory_floor_precede_all_recovery():
    assert check(authorized=False)["action"] == "PAUSE"
    assert check(memory=False)["action"] == "WAIT"


def test_transient_retries_are_bounded_and_local():
    assert check(reason="MODEL_TIMEOUT", attempts=0)["action"] == "RETRY_LOCAL"
    assert check(reason="MODEL_TIMEOUT", attempts=2)["action"] == "CONTINUE_OTHER_LOCAL"


def test_large_eligible_work_can_decompose_without_cloud():
    assert check(LARGE, attempts=0)["action"] == "DECOMPOSE_LOCAL"
    assert check(LARGE, attempts=2)["action"] == "CONTINUE_OTHER_LOCAL"


def test_missing_independent_acceptance_never_becomes_fabricated():
    assert check(LARGE, reason="NEEDS_ACCEPTANCE_EXAMPLES", attempts=0)["action"] == "CONTINUE_OTHER_LOCAL"


def test_unapproved_alternatives_are_not_dispatched():
    assert check(alternatives=[{"id": "B", "eligible": True}])["action"] == "BLOCK"
    assert check(alternatives=[])["action"] == "BLOCK"


def test_invalid_request_and_attempts_fail_closed():
    assert check({"route": {}}, attempts=0)["action"] == "BLOCK"
    assert check(attempts=-1)["reason"] == "INVALID_ATTEMPT_COUNT"
