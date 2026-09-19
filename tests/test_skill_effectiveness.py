import pytest
from project_orchestrator.skill_effectiveness import SkillLifecycle, SkillObservation, evaluate_skill, refinement_action, should_inject

def o(run,passed=True,prevented=False,skill="s"):
    return SkillObservation(skill,run,True,passed,prevented)

def test_unmeasured_skill_collects_evidence():
    e=evaluate_skill("s",[])
    assert e.state is SkillLifecycle.UNMEASURED
    assert refinement_action(e)=="COLLECT_EVIDENCE"

def test_verified_reuse_promotes_to_proven():
    e=evaluate_skill("s",[o("1"),o("2"),o("3",prevented=True)])
    assert e.state is SkillLifecycle.PROVEN and e.prevented_failures==1 and should_inject(e)

def test_weak_skill_is_degraded_and_not_injected():
    e=evaluate_skill("s",[o("1",False),o("2",False),o("3",True)])
    assert e.state is SkillLifecycle.DEGRADED
    assert not should_inject(e)
    assert refinement_action(e)=="REVIEW_OR_RETIRE"

def test_pilot_stays_advisory_until_enough_evidence():
    assert evaluate_skill("s",[o("1")]).state is SkillLifecycle.PILOT

def test_duplicate_run_cannot_inflate_effectiveness():
    with pytest.raises(ValueError,match="duplicate"): evaluate_skill("s",[o("1"),o("1")])

def test_other_skill_observations_do_not_count():
    e=evaluate_skill("s",[o("1",skill="other")])
    assert e.applications==0
