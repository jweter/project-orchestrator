from scripts import continuous_flow_supervisor as supervisor

def record(repair_eligible):
    return {
        "repository":"o/r","base_sha":"base","branch":"actor/x",
        "preserved_work_validation":"PRESERVED_VALIDATION_PASSED",
        "preserved_work_evidence":{"diff_sha256":"digest","head_sha":"base",
                                   "repair_eligible":repair_eligible},
    }

def test_untracked_preserved_work_cannot_receive_recovery_capability():
    assert supervisor.issue_preserved_recovery_capability(record(False)) is None

def test_repair_eligible_preserved_work_can_receive_recovery_capability():
    cap=supervisor.issue_preserved_recovery_capability(record(True))
    assert cap is not None
    assert cap["state"]=="ISSUED"

def test_existing_capability_cannot_bypass_repair_eligibility(tmp_path):
    r=record(False)
    r["preserved_recovery_capability"]={
        "state":"ISSUED","repository":"o/r","base_sha":"base","branch":"actor/x",
        "preserved_diff_sha256":"digest","preserved_head_sha":"base",
    }
    assert supervisor.consume_preserved_recovery_capability(tmp_path,r)=="RECOVERY_CAPABILITY_REPAIR_NOT_AUTHORIZED"
    assert r["preserved_recovery_capability"]["state"]=="ISSUED"
