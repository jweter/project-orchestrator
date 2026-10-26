import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from project_orchestrator import iron_god_owner_control as oc
from project_orchestrator.human_ownership import (
    AUTHORIZED,
    OWNER_CONTROL_ACTIVE,
    OWNER_CONTROL_UNVERIFIED,
    human_mode_active,
    owner_control_status,
    owner_state_root,
)
from tests.owner_authority import grant_owner_authority, revoke_owner_authority

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI owner control")


def flow(tmp_path: Path) -> Path:
    path = tmp_path / "flow-state"
    path.mkdir()
    return path


def test_absent_controller_state_fails_closed(tmp_path: Path):
    state = flow(tmp_path)
    assert owner_control_status(state) == OWNER_CONTROL_UNVERIFIED
    assert human_mode_active(state) is True


def test_give_me_control_blocks_autonomous_ownership(tmp_path: Path):
    state = flow(tmp_path)
    grant_owner_authority(state)
    revoke_owner_authority(state)
    assert owner_control_status(state) == OWNER_CONTROL_ACTIVE
    assert human_mode_active(state) is True


def test_tampered_controller_state_fails_closed(tmp_path: Path):
    state = flow(tmp_path)
    grant_owner_authority(state)
    with closing(sqlite3.connect(oc.control_root(state) / oc.DB_FILE)) as db:
        db.execute("UPDATE owner_commands SET mac = ?", ("0" * 64,))
        db.commit()
    assert owner_control_status(state) == OWNER_CONTROL_UNVERIFIED
    assert human_mode_active(state) is True


def test_acknowledged_owner_grant_releases_autonomous_ownership(tmp_path: Path):
    state = flow(tmp_path)
    grant_owner_authority(state)
    assert owner_control_status(state) == AUTHORIZED
    assert human_mode_active(state) is False


def test_released_retired_marker_grants_nothing(tmp_path: Path):
    state = flow(tmp_path)
    (state / "human-mode.json").write_text('{"enabled": false}\n', encoding="utf-8")
    assert owner_control_status(state) == OWNER_CONTROL_UNVERIFIED
    assert human_mode_active(state) is True


def test_nested_worker_uses_canonical_flow_state_controller(tmp_path: Path):
    state = flow(tmp_path)
    worker = state / "worker-state" / "worker-1"
    worker.mkdir(parents=True)
    revoke_owner_authority(state)
    # An authorizing controller beside the nested worker state is ignored.
    grant_owner_authority(worker)
    state_path = worker / "builder.json"
    assert owner_state_root(state_path) == state
    assert owner_control_status(owner_state_root(state_path)) == OWNER_CONTROL_ACTIVE


def test_unmarked_state_outside_flow_state_resolves_under_localappdata(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert owner_state_root(tmp_path / "loose" / "builder.json") == (
        tmp_path / "local" / "ExponentialEngine" / "flow-state"
    )
