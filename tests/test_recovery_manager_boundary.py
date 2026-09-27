import inspect

import project_orchestrator.recovery_manager as recovery
import scripts.continuous_flow_supervisor as supervisor


def test_supervisor_exposes_only_thin_ownership_recovery_adapters():
    for name in (
        "recover_stale_issued_grants",
        "recover_stale_recovery_generations",
        "recover_stale_grant_claims",
        "recheck_quarantined_claims",
        "quarantined_grants",
    ):
        source = inspect.getsource(getattr(supervisor, name))
        assert "_recovery." in source
        assert "hands-off-grant-lifecycle.json" not in source


def test_recovery_manager_owns_liveness_and_quarantine_algorithms():
    source = inspect.getsource(recovery)
    assert "CLAIM_LEASE_EXPIRED_WORKER_DEAD" in source
    assert "RECOVERY_GENERATION_OWNER_DEAD" in source
    assert "CLAIM_LEASE_UNKNOWN_QUARANTINED" in source
    assert "RELEASED_TO_RECOVERY" in source


def test_supervisor_reexports_recovery_thresholds_from_shared_module():
    assert supervisor.ISSUE_LEASE_SECONDS == recovery.ISSUE_LEASE_SECONDS
    assert supervisor.CLAIM_LEASE_SECONDS == recovery.CLAIM_LEASE_SECONDS
    assert supervisor.RECOVERY_GENERATION_LEASE_SECONDS == recovery.RECOVERY_GENERATION_LEASE_SECONDS
    assert supervisor.UNKNOWN_LIVENESS_MAX_CHECKS == recovery.UNKNOWN_LIVENESS_MAX_CHECKS


def test_quarantine_report_never_exposes_lease_token(tmp_path):
    lifecycle = tmp_path / "hands-off-grant-lifecycle.json"
    lifecycle.write_text(
        '{"grants":{"g":{"state":"QUARANTINED","worker":{"host":"h","pid":1,'
        '"process_start_fingerprint":"fp","lease_token":"secret"}}}}',
        encoding="utf-8",
    )
    report = recovery.quarantined_grants(tmp_path)
    assert "lease_token" not in report["g"]
    assert "secret" not in repr(report)
