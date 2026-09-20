import json
import pytest
import scripts.continuous_flow_supervisor as supervisor

def test_grant_issuance_is_single_writer(tmp_path):
    first=supervisor.acquire_grant_issue_lock(tmp_path)
    with pytest.raises(RuntimeError,match="GRANT_ISSUANCE_BUSY:ALIVE"):
        supervisor.acquire_grant_issue_lock(tmp_path)
    supervisor.release_grant_issue_lock(first)

def test_dead_owner_lock_is_recovered(tmp_path,monkeypatch):
    path=tmp_path/"grant-issuance.lock"
    path.write_text(json.dumps({"schema_version":1,"lock_id":"dead-lock",
        "worker":{"host":"dead","pid":999},"created_at_utc":"2026-09-20T00:00:00+00:00"}),encoding="utf-8")
    monkeypatch.setattr(supervisor,"worker_liveness",lambda worker:"DEAD")
    lock=supervisor.acquire_grant_issue_lock(tmp_path)
    assert lock["lock_id"]!="dead-lock"
    supervisor.release_grant_issue_lock(lock)

def test_unknown_owner_fails_closed(tmp_path,monkeypatch):
    path=tmp_path/"grant-issuance.lock"
    path.write_text(json.dumps({"schema_version":1,"lock_id":"unknown-lock",
        "worker":{"host":"other","pid":999},"created_at_utc":"2026-09-20T00:00:00+00:00"}),encoding="utf-8")
    monkeypatch.setattr(supervisor,"worker_liveness",lambda worker:"UNKNOWN")
    with pytest.raises(RuntimeError,match="GRANT_ISSUANCE_BUSY:UNKNOWN"):
        supervisor.acquire_grant_issue_lock(tmp_path)
    assert path.exists()

def test_unreadable_lock_fails_closed(tmp_path):
    (tmp_path/"grant-issuance.lock").write_text("{bad",encoding="utf-8")
    with pytest.raises(RuntimeError,match="GRANT_ISSUANCE_LOCK_UNREADABLE"):
        supervisor.acquire_grant_issue_lock(tmp_path)

def test_release_cannot_delete_replacement_lock(tmp_path):
    first=supervisor.acquire_grant_issue_lock(tmp_path)
    path=first["path"]
    replacement={"schema_version":1,"lock_id":"replacement","worker":first["worker"],
                 "created_at_utc":"2026-09-20T00:00:00+00:00"}
    path.write_text(json.dumps(replacement),encoding="utf-8")
    with pytest.raises(RuntimeError,match="GRANT_ISSUANCE_LOCK_OWNERSHIP_LOST"):
        supervisor.release_grant_issue_lock(first)
    assert path.exists()
