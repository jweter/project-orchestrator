"""Test fixtures that produce real, authenticated owner-controller state.

These replace writing the retired owner marker. A grant is a genuine
DPAPI-keyed Take the Wheel (desktop origin stubbed, because tests cannot run
from the desktop icon) acknowledged by the live test process acting as the
supervisor, so every consumer exercises the production decision path.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from project_orchestrator import iron_god_owner_control as oc

TEST_RELEASE_SHA = "a" * 40


def _desktop_origin() -> dict:
    return {"session_id": 1, "origin": "desktop-control"}


def grant_owner_authority(state_root: Path, *, release_sha: str = TEST_RELEASE_SHA) -> oc.SupervisorIdentity:
    """Owner Take the Wheel + live supervisor acknowledgment for ``state_root``."""
    Path(state_root).mkdir(parents=True, exist_ok=True)
    root = oc.control_root(Path(state_root))
    oc.issue_command(root, oc.TAKE_THE_WHEEL, release_sha=release_sha, origin_check=_desktop_origin)
    identity = oc.current_supervisor_identity(uuid.uuid4().hex)
    decision = oc.acknowledge_grant(root, identity, release_sha=release_sha)
    assert decision.permitted, decision
    return identity


def revoke_owner_authority(state_root: Path) -> None:
    """Owner Give Me Control for ``state_root``."""
    Path(state_root).mkdir(parents=True, exist_ok=True)
    oc.issue_command(oc.control_root(Path(state_root)), oc.GIVE_ME_CONTROL, release_sha=None)


def tamper_owner_authority(state_root: Path) -> None:
    """Corrupt every recorded command's MAC, as a forged or damaged store would be."""
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(oc.control_root(Path(state_root)) / oc.DB_FILE)) as db:
        db.execute("UPDATE owner_commands SET mac = ?", ("0" * 64,))
        db.commit()


# Unsafe owner states that must never authorize, as (scenario, owner status).
UNSAFE_OWNER_SCENARIOS = [
    ("revoked", "OWNER_CONTROL_ACTIVE"),
    ("tampered", "OWNER_CONTROL_UNVERIFIED"),
    ("retired_marker_released", "OWNER_CONTROL_UNVERIFIED"),
    ("absent", "OWNER_CONTROL_UNVERIFIED"),
]


def apply_owner_scenario(state_root: Path, scenario: str) -> None:
    """Put ``state_root`` into one named unsafe owner state."""
    state_root = Path(state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    if scenario == "revoked":
        grant_owner_authority(state_root)
        revoke_owner_authority(state_root)
    elif scenario == "tampered":
        grant_owner_authority(state_root)
        tamper_owner_authority(state_root)
    elif scenario == "retired_marker_released":
        # The retired marker saying "released" must grant nothing.
        (state_root / "human-mode.json").write_text('{"enabled": false}', encoding="utf-8")
    elif scenario != "absent":
        raise ValueError(scenario)
