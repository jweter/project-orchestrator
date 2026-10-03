import json
from datetime import UTC, datetime, timedelta

from project_orchestrator import recovery_manager


def test_recovery_mutation_uses_shared_lifecycle_writer(tmp_path, monkeypatch):
    path = tmp_path / "hands-off-grant-lifecycle.json"
    now = datetime.now(UTC)
    path.write_text(json.dumps({
        "schema_version": 1,
        "grants": {
            "g1": {
                "state": "CLAIMED",
                "claimed_at": (now - timedelta(hours=1)).isoformat(),
                "worker": {"host": "Azathoth", "pid": 123},
            }
        },
    }), encoding="utf-8")
    observed = {}

    def lifecycle_writer(target, payload):
        observed["target"] = target
        observed["payload"] = payload
        target.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(recovery_manager, "atomic_lifecycle_json", lifecycle_writer)

    recovered = recovery_manager.recover_stale_grant_claims(
        tmp_path, now, lambda _worker: "DEAD",
    )

    assert recovered == ["g1"]
    assert observed["target"] == path
    record = observed["payload"]["grants"]["g1"]
    assert record["state"] == "ABANDONED"
    assert record["result_status"] == "CLAIM_LEASE_EXPIRED_WORKER_DEAD"
