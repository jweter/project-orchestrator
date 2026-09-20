import json
import subprocess
import sys
from pathlib import Path

import scripts.continuous_flow_supervisor as supervisor


def test_supervisor_instance_is_single_owner_per_state_root(tmp_path):
    first=supervisor.acquire_supervisor_instance_lock(tmp_path)
    try:
        try:
            supervisor.acquire_supervisor_instance_lock(tmp_path)
            assert False,"second supervisor lock unexpectedly acquired"
        except RuntimeError as exc:
            assert str(exc)=="SUPERVISOR_ALREADY_RUNNING"
    finally:
        supervisor.release_supervisor_instance_lock(first)
    second=supervisor.acquire_supervisor_instance_lock(tmp_path)
    supervisor.release_supervisor_instance_lock(second)


def test_supervisor_lock_is_released_by_kernel_after_process_exit(tmp_path):
    repo_root=Path(supervisor.__file__).resolve().parents[1]
    code=(
        "from pathlib import Path;"
        "from scripts.continuous_flow_supervisor import acquire_supervisor_instance_lock;"
        f"acquire_supervisor_instance_lock(Path({str(tmp_path)!r}))"
    )
    result=subprocess.run([sys.executable,"-c",code],cwd=repo_root,
                          capture_output=True,text=True,check=False)
    assert result.returncode==0,result.stderr
    recovered=supervisor.acquire_supervisor_instance_lock(tmp_path)
    supervisor.release_supervisor_instance_lock(recovered)


def test_instance_metadata_tracks_and_cleans_exact_owner(tmp_path):
    lock=supervisor.acquire_supervisor_instance_lock(tmp_path)
    metadata=tmp_path/"continuous-flow-supervisor-instance.json"
    payload=json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["instance_id"]==lock["instance_id"]
    assert payload["pid"]>0
    supervisor.release_supervisor_instance_lock(lock)
    assert not metadata.exists()


def test_busy_main_exits_cleanly_without_starting_work(tmp_path,monkeypatch):
    monkeypatch.setattr(supervisor,"acquire_supervisor_instance_lock",
                        lambda state: (_ for _ in ()).throw(RuntimeError("SUPERVISOR_ALREADY_RUNNING")))
    called=[]
    monkeypatch.setattr(supervisor,"start_merge_watcher",lambda *a,**k: called.append("watcher"))
    monkeypatch.setattr(sys,"argv",[
        "continuous_flow_supervisor.py",
        "--repo-root",str(tmp_path),"--workspace-root",str(tmp_path),
        "--state-root",str(tmp_path),"--once",
    ])
    assert supervisor.main()==0
    assert called==[]
    wake=json.loads((tmp_path/"supervisor-wake.json").read_text(encoding="utf-8"))
    assert wake["status"]=="SUPERVISOR_ALREADY_RUNNING"
