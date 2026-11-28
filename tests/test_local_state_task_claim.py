"""IG-01: canonical SQLite task claims for multiple engineering agents.

All tests use a disposable state database. A claim never grants execution.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from project_orchestrator.local_state import (
    LeaseConflict,
    LocalStateStore,
    OwnerControlBlocked,
    task_resource_key,
)


@pytest.fixture
def store(tmp_path: Path) -> LocalStateStore:
    owner = tmp_path / "owner.json"
    owner.write_text('{"enabled": false}', encoding="utf-8")
    state = LocalStateStore(tmp_path / "engineering.sqlite", owner_state_path=owner)
    state.initialize()
    return state


def test_task_identity_normalizes_case_and_github_issue_prefix():
    canonical = task_resource_key("jweter/project-orchestrator", "810")
    assert task_resource_key("JWETER/Project-Orchestrator", "#810") == canonical
    assert task_resource_key("jweter/project-orchestrator", "IG-01") == (
        task_resource_key("jweter/PROJECT-ORCHESTRATOR", "ig-01")
    )
    assert canonical != task_resource_key("jweter/VoiceStudio", "810")
    assert canonical != task_resource_key("jweter/project-orchestrator", "811")


@pytest.mark.parametrize(
    ("repository", "task_id"),
    [
        ("project-orchestrator", "810"),
        ("jweter/project-orchestrator/extra", "810"),
        ("jweter/project-orchestrator", ""),
        ("jweter/project-orchestrator", "##810"),
        ("jweter/project-orchestrator", "810 / other"),
        ("jweter/project-orchestrator", "../810"),
    ],
)
def test_malformed_task_identity_fails_before_creating_lease(
    store: LocalStateStore, repository: str, task_id: str
):
    with pytest.raises(ValueError):
        store.claim_task(repository, task_id, "agent", "fingerprint")
    assert store.snapshot()["leases"] == []


def test_concurrent_agents_share_one_durable_task_claim(store: LocalStateStore):
    """Independent connections serialize 16 contenders; only one can own work."""

    def attempt(index: int) -> str | None:
        independent = LocalStateStore(
            store.database, owner_state_path=store.owner_state_path
        )
        try:
            identity = (
                "JWETER/Project-Orchestrator"
                if index % 2
                else "jweter/project-orchestrator"
            )
            task = "#810" if index % 2 else "810"
            return independent.claim_task(
                identity, task, f"agent-{index}", f"fp-{index}"
            )
        except LeaseConflict:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(16)))

    winners = [token for token in outcomes if token is not None]
    assert len(winners) == 1
    snapshot = store.snapshot()
    assert len(snapshot["leases"]) == 1
    assert snapshot["leases"][0]["resource"] == task_resource_key(
        "jweter/project-orchestrator", "810"
    )
    assert [event["kind"] for event in snapshot["events"]] == ["WORKER_CLAIMED"]


def test_release_requires_exact_token_and_cannot_reuse_old_token(
    store: LocalStateStore,
):
    resource = task_resource_key("jweter/VoiceStudio", "VS-01")
    first = store.claim_task("jweter/VoiceStudio", "VS-01", "claude", "fp-claude")
    with pytest.raises(LeaseConflict):
        store.claim_task("JWETER/voicestudio", "vs-01", "chatgpt", "fp-chatgpt")
    assert not store.release_lease(resource, first, "fp-chatgpt")
    assert store.release_lease(resource, first, "fp-claude")
    assert not store.heartbeat(resource, first, "fp-claude")
    with pytest.raises(LeaseConflict, match="previously used"):
        store.claim_task(
            "jweter/VoiceStudio", "VS-01", "engine", "fp-engine", lease_id=first
        )
    second = store.claim_task("jweter/VoiceStudio", "VS-01", "engine", "fp-engine")
    assert second != first
    assert len(store.snapshot()["leases"]) == 1
    assert [event["kind"] for event in store.snapshot()["events"]] == [
        "WORKER_CLAIMED",
        "WORKER_RELEASED",
        "WORKER_CLAIMED",
    ]


def test_take_the_wheel_blocks_task_claim_without_modifying_state(
    store: LocalStateStore,
):
    store.owner_state_path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    with pytest.raises(OwnerControlBlocked):
        store.claim_task("jweter/Project-Everward", "EV-01", "engine", "fp")
    assert store.snapshot()["leases"] == []
    assert store.snapshot()["events"] == []
