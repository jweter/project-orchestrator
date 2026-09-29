import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from project_orchestrator.activation_capability import ensure_portfolio_workspaces, evolution_authorized
from project_orchestrator.planning_dispatch import product_convergence
from project_orchestrator.workspace_recovery import pending_recovery_grants
from project_orchestrator.supervisor_runtime import (
    DEAD_BUILDER_STATE,
    reconcile_dead_builder_states,
)


def test_dead_builder_state_is_recovered(tmp_path):
    state = tmp_path / "state"
    worker = state / "worker-state" / "rocksmith-worker-1"
    worker.mkdir(parents=True)
    path = worker / "builder.json"
    path.write_text(json.dumps({
        "status": "BUILDER_RUNNING",
        "worker": {"host": "Azathoth", "pid": 123, "process_start_fingerprint": "old"},
    }))
    result = reconcile_dead_builder_states(
        state,
        now=datetime(2026, 9, 29, tzinfo=UTC),
        liveness_probe=lambda _: "DEAD",
    )
    payload = json.loads(path.read_text())
    assert list(result.values()) == [DEAD_BUILDER_STATE]
    assert payload["status"] == DEAD_BUILDER_STATE
    assert payload["dead_builder_reason"] == DEAD_BUILDER_STATE


def test_live_builder_state_is_preserved(tmp_path):
    state = tmp_path / "state"
    worker = state / "worker-state" / "everward-worker-1"
    worker.mkdir(parents=True)
    path = worker / "builder.json"
    path.write_text(json.dumps({
        "status": "BUILDER_RUNNING",
        "worker": {"host": "Azathoth", "pid": 456, "process_start_fingerprint": "same"},
    }))
    result = reconcile_dead_builder_states(
        state,
        liveness_probe=lambda _: "ALIVE",
    )
    assert list(result.values()) == ["BUILDER_RUNNING_OWNER_ALIVE"]
    assert json.loads(path.read_text())["status"] == "BUILDER_RUNNING"


def test_missing_voicestudio_workspace_is_provisioned(tmp_path):
    repo_root = tmp_path / "orchestrator"
    (repo_root / "engineering").mkdir(parents=True)
    (repo_root / "engineering" / "portfolio.json").write_text(json.dumps({
        "projects": [{"repositories": [{"name": "jweter/VoiceStudio"}]}]
    }))
    workspace = tmp_path / "workspaces"
    state = tmp_path / "state"
    def fake_ensure(repo, root):
        target = root / repo.split("/", 1)[1]
        target.mkdir(parents=True)
        return target

    result = ensure_portfolio_workspaces(
        repo_root,
        workspace,
        state,
        ensure_workspace_fn=fake_ensure,
    )
    assert result["status"] == "OK"
    assert result["repositories"]["jweter/VoiceStudio"]["status"] == "PROVISIONED"
    assert (workspace / "VoiceStudio").is_dir()


def test_four_product_convergence_flags_unexplained_idle(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "hands-off-grant-lifecycle.json").write_text(json.dumps({"grants": {}}))
    result = product_convergence(
        state,
        ["knowledge-engine", "rocksmith", "everward", "voicestudio"],
        current_invocation_id="burst-1",
    )
    assert result["status"] == "UNEXPLAINED_ELIGIBLE_IDLE"
    assert result["unexplained_eligible_products"] == [
        "everward", "knowledge-engine", "rocksmith", "voicestudio"
    ]



def test_activation_product_set_drift_surfaces_missing_voicestudio(tmp_path):
    repo_root = tmp_path / "orchestrator"
    (repo_root / "engineering").mkdir(parents=True)
    (repo_root / "engineering" / "hands-off-evolution.json").write_text(json.dumps({
        "products": {
            "knowledge-engine": ["jweter/knowledge-engine-core"],
            "rocksmith": ["jweter/rocksmith-cdlc-generator"],
            "everward": ["jweter/Project-Everward"],
            "voicestudio": ["jweter/VoiceStudio"],
        }
    }))
    state = tmp_path / "state"
    state.mkdir()
    (state / "hands-off-activation.json").write_text("{}")
    (state / "hands-off-live-heads.json").write_text("{}")

    payload = {
        "reason": "ACTIVATED",
        "products": {
            "knowledge-engine": {"allowed": True},
            "rocksmith": {"allowed": True},
            "everward": {"allowed": True},
        },
    }
    fake_run = lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    ok, reason, products, stale = evolution_authorized(
        repo_root,
        tmp_path / "workspaces",
        state,
        observe_fn=lambda *a, **k: (True, "LIVE_HEADS_OBSERVED"),
        run_fn=fake_run,
    )
    assert ok is True
    assert products == ["everward", "knowledge-engine", "rocksmith"]
    assert stale == ["voicestudio"]



def test_recovery_batch_is_bounded_and_skips_future_backoff(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    now = datetime(2026, 9, 29, tzinfo=UTC)
    grants = {
        "new": {"state": "ABANDONED"},
        "g0": {"state": "ABANDONED"},
        "g1": {"state": "ABANDONED"},
        "future": {
            "state": "ABANDONED",
            "recovery_next_attempt_at": (now + timedelta(hours=1)).isoformat(),
        },
    }
    (state / "hands-off-grant-lifecycle.json").write_text(json.dumps({"grants": grants}))
    result = pending_recovery_grants(
        state,
        newly_abandoned=["new"],
        now=now,
        max_batch=2,
    )
    assert result == ["new", "g0"]
