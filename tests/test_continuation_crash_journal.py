import json
import scripts.continuous_flow_supervisor as supervisor

def test_continuation_commit_journal_matches_authoritative_history(tmp_path):
    record=supervisor.record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=True,liveness_reacquire={"requested":False},burst_id="b",process_instance_id="p")
    history=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    journal=json.loads((tmp_path/"continuation-pass-journal.json").read_text())
    assert journal["status"]=="COMMITTED"
    assert journal["sequence"]==history["pass_count"]==record["sequence"]
    assert journal["record_hash"]==history["chain_head"]==record["record_hash"]
    assert journal["anchor_hash"]==history["anchor_hash"]
