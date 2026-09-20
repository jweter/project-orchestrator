import json
import subprocess
import sys
from pathlib import Path
import pytest
import scripts.continuous_flow_supervisor as supervisor

def test_grant_issuance_is_single_writer(tmp_path):
    first=supervisor.acquire_grant_issue_lock(tmp_path)
    with pytest.raises(RuntimeError,match="GRANT_ISSUANCE_BUSY"):
        supervisor.acquire_grant_issue_lock(tmp_path)
    supervisor.release_grant_issue_lock(first)

def test_kernel_lock_recovers_after_owner_process_exits(tmp_path):
    repo_root=Path(supervisor.__file__).resolve().parents[1]
    code=(
        "from pathlib import Path;"
        "from scripts.continuous_flow_supervisor import acquire_grant_issue_lock;"
        f"acquire_grant_issue_lock(Path({str(tmp_path)!r}))"
    )
    result=subprocess.run([sys.executable,"-c",code],cwd=repo_root,capture_output=True,text=True,check=False)
    assert result.returncode==0, result.stderr
    lock=supervisor.acquire_grant_issue_lock(tmp_path)
    supervisor.release_grant_issue_lock(lock)

def test_truncated_or_stale_metadata_does_not_strand_lock(tmp_path):
    (tmp_path/"grant-issuance.lock.json").write_text("{bad",encoding="utf-8")
    lock=supervisor.acquire_grant_issue_lock(tmp_path)
    current=json.loads((tmp_path/"grant-issuance.lock.json").read_text())
    assert current["lock_id"]==lock["lock_id"]
    supervisor.release_grant_issue_lock(lock)

def test_release_detects_metadata_identity_loss_but_unlocks_kernel_fence(tmp_path):
    first=supervisor.acquire_grant_issue_lock(tmp_path)
    first["metadata_path"].write_text(json.dumps({"schema_version":2,"lock_id":"replacement"}),encoding="utf-8")
    with pytest.raises(RuntimeError,match="GRANT_ISSUANCE_LOCK_OWNERSHIP_LOST"):
        supervisor.release_grant_issue_lock(first)
    second=supervisor.acquire_grant_issue_lock(tmp_path)
    supervisor.release_grant_issue_lock(second)

def test_corrupt_metadata_cannot_make_kernel_lock_stealable(tmp_path):
    first=supervisor.acquire_grant_issue_lock(tmp_path)
    first["metadata_path"].write_text("{bad",encoding="utf-8")
    with pytest.raises(RuntimeError,match="GRANT_ISSUANCE_BUSY"):
        supervisor.acquire_grant_issue_lock(tmp_path)
    with pytest.raises(RuntimeError,match="GRANT_ISSUANCE_LOCK_METADATA_UNREADABLE"):
        supervisor.release_grant_issue_lock(first)
    second=supervisor.acquire_grant_issue_lock(tmp_path)
    supervisor.release_grant_issue_lock(second)
