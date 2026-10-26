"""Owner-held calibration mode and the capability gate on Canon dispatch."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_calibration as cal
from project_orchestrator import iron_god_canon_lane as lane_mod
from project_orchestrator.runtime_leases import DEFAULT_RUNTIME_CAPACITIES, RuntimeLeasePool
from tests.owner_authority import grant_owner_authority, revoke_owner_authority
from tests.test_iron_god_canon_lane import (
    ACTIVE,
    GOOD_SOURCE,
    GOOD_TEST,
    POLICY,
    WORKER,
    Runner,
    admitted,
    release_repo,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows owner controller")

SLUG = '''import re


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
'''
LEDGER = GOOD_SOURCE  # same summarize_ledger contract as the commissioning slice
LEDGER_TEST = GOOD_TEST.replace("canon_ledger_summary", "calibration_ledger_summary")
GOOD = {
    "src/project_orchestrator/calibration_slug.py": SLUG,
    "src/project_orchestrator/calibration_ledger_summary.py": LEDGER,
    "tests/test_calibration_ledger_summary.py": LEDGER_TEST,
}


def agent(files: dict[str, str], seen: list | None = None):
    def run(worktree: Path, message: str, allowed: list[str], check) -> None:
        check()
        if seen is not None:
            seen.append(message)
        for path in allowed:
            if path in files:
                (worktree / path).parent.mkdir(parents=True, exist_ok=True)
                (worktree / path).write_text(files[path], encoding="utf-8")
    return run


@pytest.fixture
def held(tmp_path):
    repo, sha = release_repo(tmp_path)
    state = tmp_path / "ExponentialEngine" / "flow-state"
    grant_owner_authority(state, release_sha=sha)
    revoke_owner_authority(state)  # the owner holds control: the engine is stopped
    pool = RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES, tmp_path / "runtime-leases.json")
    return SimpleNamespace(repo=repo, sha=sha, state=state, pool=pool, tmp=tmp_path)


def calibrate(env, run_agent, **kwargs):
    return cal.run_calibration(
        env.state, env.repo, build_message=WORKER.worker_message, run_agent=run_agent,
        verify=WORKER.run_verification, changed_paths=WORKER.changed_paths, lease_pool=env.pool,
        **{"runs": 1, **kwargs},
    )


def test_calibration_measures_every_level_and_writes_a_bound_profile(held):
    seen: list[str] = []
    profile = calibrate(held, agent(GOOD, seen), runs=2)
    assert profile["max_passing_level"] == 3
    assert [profile["levels"][str(n)]["pass_rate"] for n in (1, 2, 3)] == [1.0, 1.0, 1.0]
    assert profile["model"] == cal.DEFAULT_MODEL and profile["hardware"] == cal.hardware_fingerprint()
    assert profile["release_sha"] == held.sha and profile["ladder_version"] == cal.LADDER_VERSION
    assert 600 <= profile["recommended_worker_timeout_s"] <= 2700
    assert json.loads(cal.profile_path(held.state).read_text(encoding="utf-8")) == profile
    assert len(seen) == 6 and all("Create or edit ONLY these files" in m for m in seen)  # production prompt
    assert all("hidden" not in m.lower() for m in seen)  # the acceptance test is never shown
    lease = held.pool.acquire(lane_mod.WORKER_LEASE_RUNTIME, "next-worker")  # model lease released
    held.pool.release("next-worker", lease.lease_id)
    assert not (lane_mod.Lane(held.state).workspaces / "calibration" / "clone").exists()


def test_a_failing_level_caps_the_profile_and_skips_higher_levels(held):
    wrong = {**GOOD, "src/project_orchestrator/calibration_ledger_summary.py":
             "def summarize_ledger(ledger):\n    return {}\n"}
    profile = calibrate(held, agent(wrong))
    assert profile["max_passing_level"] == 1
    assert profile["levels"]["2"]["pass_rate"] == 0.0
    assert profile["levels"]["3"]["status"] == "SKIPPED_LOWER_LEVEL_FAILED"
    evidence = Path(profile["levels"]["2"]["runs"][0]["evidence"])  # failed work is kept for diagnosis
    assert "return {}" in (evidence / "src/project_orchestrator/calibration_ledger_summary.py").read_text(encoding="utf-8")
    assert json.loads((evidence / "verification.json").read_text(encoding="utf-8"))["hidden"][0]["ok"] is False


def test_out_of_scope_changes_fail_the_level(held):
    def sprawl(worktree, message, allowed, check):
        agent(GOOD)(worktree, message, allowed, check)
        (worktree / "AGENTS.md").write_text("edited", encoding="utf-8")
    assert calibrate(held, sprawl)["max_passing_level"] == 0


def test_calibration_refuses_while_take_the_wheel_is_live(tmp_path):
    repo, sha = release_repo(tmp_path)
    state = tmp_path / "ExponentialEngine" / "flow-state"
    grant_owner_authority(state, release_sha=sha)
    env = SimpleNamespace(repo=repo, state=state,
                          pool=RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES, tmp_path / "runtime-leases.json"))
    with pytest.raises(cal.CalibrationRefused, match="OWNER_MUST_HOLD_CONTROL"):
        calibrate(env, agent(GOOD))
    assert not cal.profile_path(state).exists()


def test_calibration_refuses_while_a_canon_worker_is_active(held):
    with pytest.raises(cal.CalibrationRefused, match="CANON_WORKER_ACTIVE"):
        calibrate(held, agent(GOOD), lane_active=lambda: True)


def test_calibration_never_shares_the_model_lease(held):
    lease = held.pool.acquire(lane_mod.WORKER_LEASE_RUNTIME, "legacy-builder")
    with pytest.raises(cal.CalibrationRefused, match="MODEL_LEASE_BUSY"):
        calibrate(held, agent(GOOD))
    held.pool.release("legacy-builder", lease.lease_id)


def test_owner_take_the_wheel_mid_calibration_aborts_and_releases_the_lease(held):
    def grab_wheel(worktree, message, allowed, check):
        grant_owner_authority(held.state, release_sha=held.sha)
        check()
    with pytest.raises(cal.CalibrationRefused, match="OWNER_MUST_HOLD_CONTROL"):
        calibrate(held, grab_wheel)
    assert not cal.profile_path(held.state).exists()
    lease = held.pool.acquire(lane_mod.WORKER_LEASE_RUNTIME, "worker")
    held.pool.release("worker", lease.lease_id)


def profile(level=3, **overrides):
    return {"schema_version": 1, "ladder_version": cal.LADDER_VERSION, "model": cal.DEFAULT_MODEL,
            "hardware": cal.hardware_fingerprint(), "max_passing_level": level, **overrides}


@pytest.mark.parametrize(("prof", "spec", "reason"), [
    (profile(), {}, "SLICE_CAPABILITY_LEVEL_UNDECLARED"),
    (None, {"capability_level": 1}, "CALIBRATION_REQUIRED"),
    (profile(ladder_version=0), {"capability_level": 1}, "CALIBRATION_REQUIRED"),
    (profile(model="ollama/other"), {"capability_level": 1}, "CALIBRATION_STALE_MODEL"),
    (profile(hardware={"processor": "elsewhere"}), {"capability_level": 1}, "CALIBRATION_STALE_HARDWARE"),
    (profile(level=2), {"capability_level": 3}, "SLICE_EXCEEDS_CALIBRATED_CAPABILITY"),
    (profile(level=3), {"capability_level": 3}, None),
])
def test_capability_gate_is_fail_closed(prof, spec, reason):
    assert cal.capability_gate(prof, spec) == reason


def test_lane_dispatches_nothing_until_this_machine_is_calibrated(tmp_path):
    repo, sha = release_repo(tmp_path)
    state = tmp_path / "ExponentialEngine" / "flow-state"
    supervisor = grant_owner_authority(state, release_sha=sha).as_dict()
    pool = RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES, tmp_path / "runtime-leases.json")
    runner = Runner({"src/project_orchestrator/canon_ledger_summary.py": GOOD_SOURCE,
                     "tests/test_canon_ledger_summary.py": GOOD_TEST})

    def step():
        return lane_mod.advance_commissioning_lane(
            state, repo, supervisor=supervisor, release_sha=sha, spawn=runner.spawn,
            alive=lambda _w: False, stop=lambda _w: None, memory_root=tmp_path / "memory",
            resources=admitted, lease_pool=pool)

    waiting = step()
    assert waiting["status"] == "WAITING_FOR_CALIBRATION" and waiting["reason"] == "CALIBRATION_REQUIRED"
    assert runner.spawned == 0 and not (lane_mod.Lane(state).worktrees / ACTIVE["slice_id"]).exists()
    cal.profile_path(state).write_text(json.dumps(profile(level=2)), encoding="utf-8")
    assert step()["reason"] == "SLICE_EXCEEDS_CALIBRATED_CAPABILITY" and runner.spawned == 0
    cal.profile_path(state).write_text(json.dumps(profile(level=3)), encoding="utf-8")
    assert step()["status"] == "DISPATCHED" and runner.spawned == 1


def test_every_commissioning_slice_declares_a_capability_level():
    for spec in POLICY["slices"]:
        assert type(spec.get("capability_level")) is int and 1 <= spec["capability_level"] <= len(cal.LADDER)


def test_calibration_model_matches_the_worker_model():
    from scripts import autonomous_slice_builder
    assert cal.DEFAULT_MODEL == autonomous_slice_builder.MODEL


def test_ladder_rungs_are_production_shaped_canon_specs(tmp_path):
    for rung in cal.LADDER:
        packet = lane_mod.build_packet(cal.calibration_spec(rung), POLICY, base_sha="a" * 40, worktree=tmp_path)
        message = WORKER.worker_message(packet)
        assert all(path in message for path in rung["allowed_paths"])
        assert rung["hidden_test"] not in message
