import json
from scripts import continuous_flow_supervisor as supervisor

class R:
    def __init__(self,returncode=0,stdout="",stderr=""):
        self.returncode=returncode; self.stdout=stdout; self.stderr=stderr

def test_verify_durable_pr_open_requires_exact_number_and_head(tmp_path,monkeypatch):
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:R(stdout=json.dumps(
        {"number":42,"state":"OPEN","headRefOid":"abc"})))
    assert supervisor.verify_durable_pr("o/r",tmp_path,42,"abc")=="RECOVERY_DURABLE_PR_HANDOFF"
    assert supervisor.verify_durable_pr("o/r",tmp_path,42,"different")=="RECOVERY_DURABLE_PR_IDENTITY_MISMATCH"

def test_verify_durable_pr_distinguishes_merged_and_closed(tmp_path,monkeypatch):
    state={"value":"MERGED"}
    def fake(*a,**k):
        return R(stdout=json.dumps({"number":7,"state":state["value"],"headRefOid":"head"}))
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    assert supervisor.verify_durable_pr("o/r",tmp_path,7,"head")=="RECOVERY_DURABLE_PR_MERGED"
    state["value"]="CLOSED"
    assert supervisor.verify_durable_pr("o/r",tmp_path,7,"head")=="RECOVERY_DURABLE_PR_CLOSED_UNMERGED"

def test_verify_durable_pr_fails_closed_on_unreadable_or_unknown(tmp_path,monkeypatch):
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:R(returncode=1))
    assert supervisor.verify_durable_pr("o/r",tmp_path,1,"head")=="RECOVERY_DURABLE_PR_UNREADABLE"
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:R(stdout=json.dumps(
        {"number":1,"state":"MYSTERY","headRefOid":"head"})))
    assert supervisor.verify_durable_pr("o/r",tmp_path,1,"head")=="RECOVERY_DURABLE_PR_STATE_UNKNOWN"
