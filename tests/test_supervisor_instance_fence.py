import json, sys
from pathlib import Path
import scripts.continuous_flow_supervisor as supervisor

def test_second_supervisor_instance_is_fenced_until_release(tmp_path):
    first=supervisor.acquire_supervisor_instance_lock(tmp_path)
    assert first is not None
    try:
        second=supervisor.acquire_supervisor_instance_lock(tmp_path)
        assert second is None
        meta=json.loads((tmp_path/"supervisor-instance.lock.json").read_text())
        assert meta["lock_id"]==first["lock_id"]
        assert meta["worker"]["pid"]>0
    finally:
        supervisor.release_supervisor_instance_lock(first)
    third=supervisor.acquire_supervisor_instance_lock(tmp_path)
    assert third is not None
    supervisor.release_supervisor_instance_lock(third)

def test_release_does_not_delete_newer_metadata(tmp_path):
    first=supervisor.acquire_supervisor_instance_lock(tmp_path)
    assert first is not None
    meta=tmp_path/"supervisor-instance.lock.json"
    newer={"schema_version":1,"lock_id":"new-owner","worker":{"pid":999}}
    supervisor.atomic(meta,newer)
    supervisor.release_supervisor_instance_lock(first)
    assert json.loads(meta.read_text())["lock_id"]=="new-owner"

def test_main_exits_cleanly_when_supervisor_already_active(tmp_path, monkeypatch):
    repo=tmp_path/"repo"; repo.mkdir()
    work=tmp_path/"work"; work.mkdir()
    state=tmp_path/"state"
    monkeypatch.setattr(supervisor,"acquire_supervisor_instance_lock",lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys,"argv",["supervisor",
        "--repo-root",str(repo),"--workspace-root",str(work),"--state-root",str(state),"--once"])
    assert supervisor.main()==0
    payload=json.loads((state/"supervisor-reentry.json").read_text())
    assert payload["status"]=="SUPERVISOR_INSTANCE_ALREADY_ACTIVE"


def test_machine_wide_fence_blocks_different_state_roots(tmp_path):
    machine=tmp_path/"machine"
    first_state=tmp_path/"state-a"
    second_state=tmp_path/"state-b"
    first=supervisor.acquire_supervisor_instance_lock(first_state,machine)
    assert first is not None
    try:
        second=supervisor.acquire_supervisor_instance_lock(second_state,machine)
        assert second is None
        meta=json.loads((machine/"continuous-flow-supervisor.lock.json").read_text())
        assert meta["lock_id"]==first["lock_id"]
        assert meta["state_root"]==str(first_state.resolve())
    finally:
        supervisor.release_supervisor_instance_lock(first)
    third=supervisor.acquire_supervisor_instance_lock(second_state,machine)
    assert third is not None
    supervisor.release_supervisor_instance_lock(third)
