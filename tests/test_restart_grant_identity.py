import json
from pathlib import Path
import pytest
from scripts.continuous_flow_supervisor import conflicting_nonterminal_grant

SLICE={"capability_id":"cap-1","slice_id":"slice-1","repository":"jweter/repo","exact_head":"a"*40}

def write_lifecycle(root,state="CLAIMED",authority=None):
    payload={"schema_version":1,"grants":{"grant-old":{
        "state":state,"authorized_repositories":["jweter/repo"],
        "execution_slice":authority or SLICE,"worker":{"host":"host","pid":123}}}}
    (root/"hands-off-grant-lifecycle.json").write_text(json.dumps(payload),encoding="utf-8")

def test_claimed_same_slice_blocks_parallel_ownership(tmp_path):
    write_lifecycle(tmp_path)
    conflict=conflicting_nonterminal_grant(tmp_path,"jweter/repo",SLICE)
    assert conflict["grant_id"]=="grant-old"
    assert conflict["state"]=="CLAIMED"

def test_quarantined_same_slice_remains_fenced(tmp_path):
    write_lifecycle(tmp_path,state="QUARANTINED")
    assert conflicting_nonterminal_grant(tmp_path,"jweter/repo",SLICE)["state"]=="QUARANTINED"

def test_terminal_old_grant_does_not_block_new_work(tmp_path):
    write_lifecycle(tmp_path,state="COMPLETED")
    assert conflicting_nonterminal_grant(tmp_path,"jweter/repo",SLICE) is None

def test_different_slice_in_same_repo_is_fenced(tmp_path):
    write_lifecycle(tmp_path)
    other=dict(SLICE,slice_id="slice-2")
    conflict=conflicting_nonterminal_grant(tmp_path,"jweter/repo",other)
    assert conflict["grant_id"]=="grant-old"
    assert conflict["state"]=="CLAIMED"

def test_unreadable_lifecycle_fails_closed(tmp_path):
    (tmp_path/"hands-off-grant-lifecycle.json").write_text("{bad",encoding="utf-8")
    with pytest.raises(RuntimeError,match="GRANT_LIFECYCLE_UNREADABLE"):
        conflicting_nonterminal_grant(tmp_path,"jweter/repo",SLICE)


def test_transient_lifecycle_permission_error_retries(tmp_path,monkeypatch):
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"grants":{}}),encoding="utf-8")
    original=Path.read_text
    attempts={"count":0}
    def flaky_read_text(self,*args,**kwargs):
        if self==lifecycle and attempts["count"]<2:
            attempts["count"]+=1
            raise PermissionError("atomic replace in progress")
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,"read_text",flaky_read_text)
    assert conflicting_nonterminal_grant(tmp_path,"jweter/repo",SLICE) is None
    assert attempts["count"]==2
