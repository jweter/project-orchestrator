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


def test_prepared_without_history_commit_aborts_and_reuses_sequence(tmp_path):
    journal={"schema_version":1,"status":"PREPARED","sequence":1,
             "record_hash":"r","anchor_hash":"a"}
    (tmp_path/"continuation-pass-journal.json").write_text(json.dumps(journal))
    result=supervisor.reconcile_continuation_journal(tmp_path)
    assert result["status"]=="RECOVERED_ABORTED"
    recovered=json.loads((tmp_path/"continuation-pass-journal.json").read_text())
    assert recovered["status"]=="ABORTED"
    record=supervisor.record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=True,liveness_reacquire={"requested":False},burst_id="b",process_instance_id="p")
    assert record["sequence"]==1

def test_prepared_with_matching_history_promotes_to_committed(tmp_path):
    record=supervisor.record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=True,liveness_reacquire={"requested":False},burst_id="b",process_instance_id="p")
    history=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    journal={"schema_version":1,"status":"PREPARED","sequence":record["sequence"],
             "record_hash":record["record_hash"],"anchor_hash":history["anchor_hash"]}
    (tmp_path/"continuation-pass-journal.json").write_text(json.dumps(journal))
    result=supervisor.reconcile_continuation_journal(tmp_path)
    assert result["status"]=="RECOVERED_COMMITTED"
    assert json.loads((tmp_path/"continuation-pass-journal.json").read_text())["status"]=="COMMITTED"

def test_committed_journal_history_divergence_fails_closed(tmp_path):
    supervisor.record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=True,liveness_reacquire={"requested":False},burst_id="b",process_instance_id="p")
    history_path=tmp_path/"continuation-pass-history.json"
    history=json.loads(history_path.read_text())
    history["chain_head"]="forged"
    history_path.write_text(json.dumps(history))
    import pytest
    with pytest.raises(RuntimeError,match="diverges"):
        supervisor.reconcile_continuation_journal(tmp_path)


def test_malformed_existing_journal_fails_closed(tmp_path):
    (tmp_path/"continuation-pass-journal.json").write_text("{truncated")
    import pytest
    with pytest.raises(RuntimeError,match="malformed"):
        supervisor.record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
            lifecycle_advanced=True,liveness_reacquire={"requested":False},burst_id="b",process_instance_id="p")

def test_committed_journal_with_tampered_retained_record_fails_closed(tmp_path):
    supervisor.record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=True,liveness_reacquire={"requested":False},burst_id="b",process_instance_id="p")
    path=tmp_path/"continuation-pass-history.json"
    history=json.loads(path.read_text())
    history["passes"][-1]["builder_status"]="TAMPERED"
    path.write_text(json.dumps(history))
    import pytest
    with pytest.raises(RuntimeError,match="invalid history"):
        supervisor.record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
            lifecycle_advanced=True,liveness_reacquire={"requested":False},burst_id="b",process_instance_id="p")
