import copy, hashlib, json
import scripts.continuous_flow_supervisor as supervisor

def _record(root,burst="b",process="p"):
    return supervisor.record_continuation_pass(root,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=True,liveness_reacquire={"requested":False},
        burst_id=burst,process_instance_id=process)

def test_hash_chain_links_consecutive_continuation_records(tmp_path):
    first=_record(tmp_path)
    second=_record(tmp_path)
    history=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    assert history["schema_version"]==4
    assert history["passes"][0]["record_hash"]==first["record_hash"]
    assert history["passes"][1]["previous_hash"]==first["record_hash"]
    assert history["chain_head"]==second["record_hash"]

def test_tampered_retained_record_invalidates_chain_authority(tmp_path):
    _record(tmp_path); _record(tmp_path)
    path=tmp_path/"continuation-pass-history.json"
    history=json.loads(path.read_text())
    history["passes"][0]["builder_status"]="TAMPERED"
    path.write_text(json.dumps(history))
    import pytest
    with pytest.raises(RuntimeError,match="invalid history"):
        _record(tmp_path,"new","new-process")

def test_recomputed_old_record_hash_still_breaks_successor_link(tmp_path):
    _record(tmp_path); _record(tmp_path)
    path=tmp_path/"continuation-pass-history.json"
    history=json.loads(path.read_text())
    first=history["passes"][0]
    first["builder_status"]="TAMPERED"
    material={k:v for k,v in first.items() if k!="record_hash"}
    first["record_hash"]=hashlib.sha256(json.dumps(material,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    path.write_text(json.dumps(history))
    import pytest
    with pytest.raises(RuntimeError,match="invalid history"):
        _record(tmp_path)


def test_hash_chain_survives_bounded_history_truncation(tmp_path):
    for i in range(105):
        _record(tmp_path,burst=f"b{i}",process="p")
    history=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    assert history["pass_count"]==105
    assert len(history["passes"])==100
    assert history["passes"][0]["sequence"]==6
    assert history["retained_predecessor_hash"]==history["passes"][0]["previous_hash"]
    assert history["chain_head"]==history["passes"][-1]["record_hash"]
    assert _record(tmp_path)["sequence"]==106
