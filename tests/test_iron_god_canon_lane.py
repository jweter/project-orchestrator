"""Simulated single-worker Canon cycle on disposable repositories.

Real Git, real worktrees, real pytest/ruff verification and the real worker
code path; only the coding agent is a stand-in. This is NOT a live cycle.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_canon_lane as lane_mod
from project_orchestrator import iron_god_owner_control as oc
from project_orchestrator import supervisor_runtime
from project_orchestrator.runtime_leases import DEFAULT_RUNTIME_CAPACITIES, RuntimeLeasePool
from project_orchestrator.shared_memory import load_shared_memory
from project_orchestrator.worker_transport import (
    WorkerTransportError,
    accept_locally,
    load_ledger,
    record_independent_verification,
)
from tests.owner_authority import grant_owner_authority, revoke_owner_authority

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows owner controller")

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "engineering" / "iron-god-commissioning.json").read_text(encoding="utf-8"))
# The commissioning slice the lane dispatches first (earlier slices may be superseded).
ACTIVE = next(s for s in POLICY["slices"] if not s.get("superseded_by"))
SID = ACTIVE["slice_id"]
SPEC = importlib.util.spec_from_file_location("canon_worker_under_test", ROOT / "scripts" / "iron_god_canon_worker.py")
WORKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER)

GOOD_SOURCE = '''from copy import deepcopy


def summarize_ledger(ledger):
    data = deepcopy(ledger) if isinstance(ledger, dict) else {}
    packets = data.get("packets") or {}
    events = data.get("events") or []
    counts = {}
    for record in packets.values():
        state = record.get("state")
        counts[state] = counts.get(state, 0) + 1
    return {
        "packet_count": len(packets),
        "state_counts": dict(sorted(counts.items())),
        "accepted_refs": sorted(r["accepted_ref"] for r in packets.values() if r.get("accepted_ref")),
        "last_event_type": events[-1].get("type") if events else None,
    }
'''
GOOD_TEST = '''from project_orchestrator.canon_ledger_summary import summarize_ledger


def test_empty():
    assert summarize_ledger({})["packet_count"] == 0


def test_two_packets():
    summary = summarize_ledger({"packets": {"a": {"state": "X"}, "b": {"state": "Y"}}, "events": []})
    assert summary["state_counts"] == {"X": 1, "Y": 1}
'''
# Wrong: counts every packet as one state and ignores refs, yet its own weak test passes.
WRONG_SOURCE = '''def summarize_ledger(ledger):
    packets = ledger.get("packets", {})
    return {"packet_count": len(packets), "state_counts": {}, "accepted_refs": [], "last_event_type": None}
'''
WEAK_TEST = '''from project_orchestrator.canon_ledger_summary import summarize_ledger


def test_empty():
    assert summarize_ledger({})["packet_count"] == 0
'''


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def release_repo(tmp_path: Path, policy: dict | None = None) -> tuple[Path, str]:
    repo = tmp_path / "release"
    (repo / "src" / "project_orchestrator").mkdir(parents=True)
    (repo / "src" / "project_orchestrator" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "engineering").mkdir()
    (repo / "engineering" / "iron-god-commissioning.json").write_text(json.dumps(policy or POLICY), encoding="utf-8")
    _git(tmp_path, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "release@example.com")
    _git(repo, "config", "user.name", "Release")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "accepted release")
    return repo, _git(repo, "rev-parse", "HEAD")


def agent(files: dict[str, str]):
    def run(worktree: Path, message: str, allowed: list[str], check) -> None:
        assert "Create or edit ONLY these files" in message
        check()
        for relative, content in files.items():
            target = worktree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    return run


class Runner:
    """Stand-in spawn: runs the real worker code path in-process, then exits."""

    def __init__(self, files: dict[str, str], authority=lambda: True):
        self.files = files
        self.authority = authority
        self.spawned = 0

    def spawn(self, release_repo, packet_path, handback_path, *, release_sha, log_path):
        self.spawned += 1
        packet = json.loads(Path(packet_path).read_text(encoding="utf-8"))
        handback = WORKER.execute_packet(packet, authority=self.authority, run_agent=agent(self.files))
        WORKER._write(Path(handback_path), handback)
        return {"pid": 1, "process_start_fingerprint": "windows:1", "host": "fake"}


@pytest.fixture
def engine(tmp_path):
    repo, sha = release_repo(tmp_path)
    state = tmp_path / "ExponentialEngine" / "flow-state"
    supervisor = grant_owner_authority(state, release_sha=sha)
    pool = RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES, tmp_path / "runtime-leases.json")
    return SimpleNamespace(repo=repo, sha=sha, state=state, supervisor=supervisor.as_dict(),
                           memory=tmp_path / "shared-memory", tmp=tmp_path, pool=pool)


def admitted():
    return {"admitted": True, "reason": "TEST_CAPACITY"}


def calibrated(_spec):
    return None  # the machine is calibrated for every slice (gate covered in test_iron_god_calibration)


def step(engine, runner, **kwargs):
    kwargs.setdefault("resources", admitted)
    kwargs.setdefault("lease_pool", engine.pool)
    kwargs.setdefault("capability", calibrated)
    return lane_mod.advance_commissioning_lane(
        engine.state, engine.repo, supervisor=engine.supervisor, release_sha=engine.sha,
        spawn=runner.spawn, alive=lambda _w: False, stop=lambda _w: None,
        memory_root=engine.memory, **kwargs,
    )


def ledger(engine):
    return load_ledger(lane_mod.Lane(engine.state).ledger_path)


def test_full_simulated_cycle_accepts_locally_with_independent_verification_and_memory(engine):
    runner = Runner({"src/project_orchestrator/canon_ledger_summary.py": GOOD_SOURCE,
                     "tests/test_canon_ledger_summary.py": GOOD_TEST})
    dispatched = step(engine, runner)
    assert dispatched["status"] == "DISPATCHED" and runner.spawned == 1
    assert dispatched["base_sha"] == engine.sha
    lane = lane_mod.Lane(engine.state)
    packet = json.loads((lane.packets / f"{SID}.json").read_text(encoding="utf-8"))
    assert packet["paths"] == sorted(ACTIVE["allowed_paths"])
    assert "supervisor_acceptance_test" not in json.dumps(packet)  # hidden from the worker
    accepted = step(engine, runner)
    assert accepted["status"] == "ACCEPTED_LOCAL", accepted
    assert accepted["verification_passed"] is True
    head = accepted["head_sha"]
    assert head != engine.sha
    assert _git(lane.dev_repo, "rev-parse", f"refs/iron-god/accepted/{SID.lower()}") == head
    record = ledger(engine)["packets"][SID]
    assert record["state"] == "ACCEPTED_LOCAL" and record["independent_verification"] == "VERIFIED_GREEN"
    assert record["merge_authority"] is False
    events = [e["type"] for e in ledger(engine)["events"]]
    assert events == ["DISPATCHED", "STATE_TRANSITION", "WORKER_RESULT", "STATE_TRANSITION",
                      "INDEPENDENT_VERIFICATION", "LOCAL_ACCEPTANCE"]
    report = json.loads((lane.reports / f"{SID}-verification.json").read_text(encoding="utf-8"))
    names = {c["name"] for c in report["checks"]}
    assert any("supervisor_acceptance" in n for n in names)
    lessons = load_shared_memory(engine.memory)["entries"]
    assert len(lessons) == 1 and lessons[0]["source_ref"].endswith("@" + head)
    assert lessons[0]["evidence_state"] == "VERIFIED"
    # The release repository itself is untouched.
    assert _git(engine.repo, "rev-parse", "HEAD") == engine.sha
    assert _git(engine.repo, "status", "--porcelain") == ""
    assert step(engine, runner)["status"] == "IDLE_NO_READY_SLICE"
    assert runner.spawned == 1


def test_worker_pass_is_not_acceptance_hidden_supervisor_test_blocks_wrong_code(engine):
    runner = Runner({"src/project_orchestrator/canon_ledger_summary.py": WRONG_SOURCE,
                     "tests/test_canon_ledger_summary.py": WEAK_TEST})
    step(engine, runner)
    blocked = step(engine, runner)
    assert blocked["worker_status"] == "PASS"  # the worker's own weak test passed
    assert blocked["status"] == "BLOCKED" and blocked["verification_passed"] is False
    assert any("supervisor_acceptance" in name for name in blocked["failed_checks"])
    record = ledger(engine)["packets"][SID]
    assert record["independent_verification"] == "VERIFIED_FAIL"
    lane = lane_mod.Lane(engine.state)
    assert _git(lane.dev_repo, "for-each-ref", "refs/iron-god/accepted") == ""
    assert not (engine.memory / "memory.json").exists()  # failures are not lessons


def test_forbidden_path_change_is_refused_by_the_worker_and_blocked(engine):
    runner = Runner({"src/project_orchestrator/canon_ledger_summary.py": GOOD_SOURCE,
                     "tests/test_canon_ledger_summary.py": GOOD_TEST,
                     "engineering/iron-god-commissioning.json": "{}"})
    step(engine, runner)
    blocked = step(engine, runner)
    assert blocked["worker_status"] == "REVIEW_REQUIRED" and blocked["status"] == "BLOCKED"
    assert "CHANGES_OUTSIDE_ALLOWED_PATHS" in blocked["worker_summary"]


def test_no_owner_authority_dispatches_nothing(engine):
    revoke_owner_authority(engine.state)
    runner = Runner({})
    result = step(engine, runner)
    assert result["status"] == "WAITING_FOR_OWNER_AUTHORITY" and runner.spawned == 0
    assert result["reasons"] == ["OWNER_CONTROL_ACTIVE"]


def test_revocation_stops_the_active_worker_and_preserves_its_worktree(engine):
    stopped = []
    runner = Runner({})
    lane_mod.advance_commissioning_lane(
        engine.state, engine.repo, supervisor=engine.supervisor, release_sha=engine.sha,
        spawn=lambda *a, **k: {"pid": 99, "process_start_fingerprint": "windows:9", "host": "fake"},
        alive=lambda _w: True, stop=stopped.append, memory_root=engine.memory,
        resources=admitted, lease_pool=engine.pool, capability=calibrated,
    )
    implementing = lane_mod.advance_commissioning_lane(
        engine.state, engine.repo, supervisor=engine.supervisor, release_sha=engine.sha,
        spawn=runner.spawn, alive=lambda _w: True, stop=stopped.append, memory_root=engine.memory,
    )
    assert implementing["status"] == "IMPLEMENTING"
    revoke_owner_authority(engine.state)
    result = lane_mod.advance_commissioning_lane(
        engine.state, engine.repo, supervisor=engine.supervisor, release_sha=engine.sha,
        spawn=runner.spawn, alive=lambda _w: True, stop=stopped.append, memory_root=engine.memory,
    )
    assert result["status"] == "WORKER_STOPPED_AUTHORITY_REVOKED"
    assert stopped and stopped[0]["pid"] == 99
    assert Path(result["worktree_preserved"]).is_dir()
    assert ledger(engine)["packets"][SID]["state"] == "CANCELLED"


def test_worker_refuses_to_start_or_continue_without_authority(engine, tmp_path):
    runner = Runner({"src/project_orchestrator/canon_ledger_summary.py": GOOD_SOURCE}, authority=lambda: False)
    step(engine, runner)
    parked = step(engine, runner)
    assert parked["worker_status"] == "ENVIRONMENT_FAILURE"
    assert parked["worker_summary"] == "EXECUTION_AUTHORITY_DENIED_BEFORE_START"
    assert parked["status"] == "PARKED"


def test_dependent_slice_waits_for_accepted_dependency(tmp_path):
    first = dict(ACTIVE, slice_id="CANON-COMMISSION-001")
    second = dict(ACTIVE, slice_id="CANON-COMMISSION-002", depends_on=["CANON-COMMISSION-001"])
    policy = {**POLICY, "slices": [first, second]}
    assert lane_mod.next_ready_slice(policy, {"packets": {}})["slice_id"] == "CANON-COMMISSION-001"
    blocked = {"packets": {"CANON-COMMISSION-001": {"state": "BLOCKED"}}}
    assert lane_mod.next_ready_slice(policy, blocked) is None
    accepted = {"packets": {"CANON-COMMISSION-001": {"state": "ACCEPTED_LOCAL", "exact_head_sha": "a" * 40}}}
    assert lane_mod.next_ready_slice(policy, accepted)["slice_id"] == "CANON-COMMISSION-002"


def test_privileged_events_bind_the_exact_handback(engine):
    runner = Runner({"src/project_orchestrator/canon_ledger_summary.py": GOOD_SOURCE,
                     "tests/test_canon_ledger_summary.py": GOOD_TEST})
    step(engine, runner)
    step(engine, runner)
    accepted = ledger(engine)
    with pytest.raises(WorkerTransportError):
        record_independent_verification(accepted, SID, verified_head_sha="b" * 40,
                                        passed=True, checks=[{"name": "x", "ok": True}], evidence_ref="e")
    with pytest.raises(WorkerTransportError):
        accept_locally(accepted, SID, accepted_ref="refs/iron-god/x",
                       accepted_sha="b" * 40, evidence_ref="e")
    tampered = json.loads(json.dumps(accepted))
    tampered["events"][-2]["exact_head_sha"] = "c" * 40
    with pytest.raises(WorkerTransportError):
        load_ledger_payload(tampered)


def load_ledger_payload(payload):
    from project_orchestrator.worker_transport import validate_ledger
    return validate_ledger(payload)


def test_commissioning_hook_never_dispatches_legacy_products_and_contains_errors(tmp_path):
    repo, sha = release_repo(tmp_path)
    state = tmp_path / "flow-state"
    state.mkdir()

    def boom(*_a, **_k):
        raise RuntimeError("clone exploded")

    assert supervisor_runtime.commissioning_products(
        state, repo, ["iron-god", "everward"], supervisor={}, release_sha=sha, advance=boom
    ) == []
    status = json.loads((state / "iron-god-canon" / "lane-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "LANE_ERROR" and "clone exploded" in status["error"]
    empty = tmp_path / "no-policy"
    empty.mkdir()
    assert supervisor_runtime.commissioning_products(
        state, empty, ["everward"], supervisor={}, release_sha=sha
    ) == []
    status = json.loads((state / "iron-god-canon" / "lane-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "COMMISSIONING_POLICY_UNAVAILABLE"


def test_supervisor_routes_dispatch_through_the_commissioning_lane():
    source = (ROOT / "scripts" / "continuous_flow_supervisor.py").read_text(encoding="utf-8")
    assert "_supervisor_runtime.commissioning_products(state,repo,products," in source
    assert "iron-god-exclusive.json" not in source
    assert oc.CONTROL_DIRNAME  # controller remains the only authority input


def test_low_resources_wait_before_any_worktree_or_lease(engine):
    runner = Runner({})
    result = step(engine, runner, resources=lambda: {"admitted": False, "reason": "CRITICAL_MEMORY_NO_NEW_WORK"})
    assert result["status"] == "WAITING_FOR_RESOURCES" and runner.spawned == 0
    assert not (lane_mod.Lane(engine.state).worktrees / SID).exists()
    # No lease is held for the refused dispatch: another builder can take it.
    other = RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES, engine.tmp / "runtime-leases.json")
    lease = other.acquire(lane_mod.WORKER_LEASE_RUNTIME, "some-other-builder")
    other.release("some-other-builder", lease.lease_id)


def test_worker_holds_the_exclusive_model_lease_until_harvest(engine):
    runner = Runner({"src/project_orchestrator/canon_ledger_summary.py": GOOD_SOURCE,
                     "tests/test_canon_ledger_summary.py": GOOD_TEST})
    other = RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES, engine.tmp / "runtime-leases.json")
    step(engine, runner)
    with pytest.raises(Exception):
        other.acquire(lane_mod.WORKER_LEASE_RUNTIME, "some-other-builder")
    assert step(engine, runner)["status"] == "ACCEPTED_LOCAL"
    lease = other.acquire(lane_mod.WORKER_LEASE_RUNTIME, "some-other-builder")
    other.release("some-other-builder", lease.lease_id)


def test_busy_model_lease_defers_dispatch(engine):
    blocker = RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES, engine.tmp / "runtime-leases.json")
    held = blocker.acquire(lane_mod.WORKER_LEASE_RUNTIME, "legacy-builder")
    runner = Runner({})
    result = step(engine, runner)
    assert result["status"] == "WAITING_FOR_RUNTIME_LEASE" and runner.spawned == 0
    blocker.release("legacy-builder", held.lease_id)


def test_commissioning_suspends_legacy_product_activity(tmp_path, monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor

    repo, _sha = release_repo(tmp_path)
    state = tmp_path / "flow-state"
    state.mkdir()
    (state / "linear-bindings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *a, **k: pytest.fail("no merge watcher process"))
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *a, **k: pytest.fail("no product subprocess"))
    watcher = supervisor.start_merge_watcher(repo, state, 60)
    # The stand-in is safe at every use in main(): restart check, report, terminate.
    assert watcher.poll() == 0 and watcher.pid is None and watcher.terminate() is None
    assert supervisor.run_linear_reconciliation(repo, state) is None
    assert supervisor.evolution_authorized(repo, tmp_path, state) == (
        False, "IRON_GOD_COMMISSIONING_LEGACY_READINESS_SKIPPED", [], [],
    )


def test_without_commissioning_policy_legacy_activity_is_unchanged(tmp_path, monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor

    repo = tmp_path / "plain"
    repo.mkdir()
    started = []
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *a, **k: started.append(a) or "proc")
    assert supervisor.start_merge_watcher(repo, tmp_path, 60) == "proc" and started
    assert supervisor_runtime.commissioning_active(repo) is False


class CrashingRunner(Runner):
    """Stand-in for the live failure: the agent crashes after editing files."""

    def spawn(self, release_repo, packet_path, handback_path, *, release_sha, log_path):
        self.spawned += 1
        packet = json.loads(Path(packet_path).read_text(encoding="utf-8"))

        def crash(worktree, message, allowed, check):
            (worktree / "src" / "project_orchestrator" / "canon_ledger_summary.py").write_text("", encoding="utf-8")
            raise RuntimeError("AIDER_EXIT_1: UnicodeEncodeError")

        WORKER._write(Path(handback_path), WORKER.execute_packet(packet, authority=lambda: True, run_agent=crash))
        return {"pid": 1, "process_start_fingerprint": "windows:1", "host": "fake"}


def new_release(engine):
    (engine.repo / "RUNNER_FIX.txt").write_text(f"remediated runner after {engine.sha}", encoding="utf-8")
    _git(engine.repo, "add", ".")
    _git(engine.repo, "commit", "-q", "-m", "remediate runner")
    engine.sha = _git(engine.repo, "rev-parse", "HEAD")
    engine.supervisor = grant_owner_authority(engine.state, release_sha=engine.sha).as_dict()


def test_environment_failure_retries_only_under_a_remediated_release(engine):
    step(engine, CrashingRunner({}))
    parked = step(engine, CrashingRunner({}))
    assert parked["status"] == "PARKED" and parked["worker_status"] == "ENVIRONMENT_FAILURE"
    original = engine.sha
    same = CrashingRunner({})
    idle = step(engine, same)  # unchanged release: never burn the retry budget
    assert idle["status"] == "IDLE_NO_READY_SLICE" and same.spawned == 0
    assert idle["awaiting_remediation"] == [SID]

    new_release(engine)
    good = Runner({"src/project_orchestrator/canon_ledger_summary.py": GOOD_SOURCE,
                   "tests/test_canon_ledger_summary.py": GOOD_TEST})
    retried = step(engine, good)
    assert retried["status"] == "RETRY_DISPATCHED" and retried["attempt"] == 2
    assert retried["base_sha"] == original  # same exact base and branch as the failed attempt
    preserved = Path(retried["preserved_worktree"])
    assert (preserved / "src" / "project_orchestrator" / "canon_ledger_summary.py").exists()  # evidence kept
    accepted = step(engine, good)
    assert accepted["status"] == "ACCEPTED_LOCAL", accepted
    record = ledger(engine)["packets"][SID]
    assert record["attempt_count"] == 2 and record["previous_results"][0]["status"] == "ENVIRONMENT_FAILURE"
    retry = [e for e in ledger(engine)["events"] if e["type"] == "RETRY"]
    assert [e["evidence_ref"] for e in retry] == [f"runner-release:{engine.sha}"]


def test_retry_budget_is_bounded_by_canon(engine):
    step(engine, CrashingRunner({}))
    step(engine, CrashingRunner({}))
    for attempt in (2, 3):
        new_release(engine)
        assert step(engine, CrashingRunner({}))["attempt"] == attempt
        assert step(engine, CrashingRunner({}))["status"] == "PARKED"
    new_release(engine)
    exhausted = CrashingRunner({})
    assert step(engine, exhausted)["status"] == "IDLE_NO_READY_SLICE" and exhausted.spawned == 0


def test_superseded_parked_slice_is_cancelled_and_its_replan_dispatched(tmp_path):
    original = dict(POLICY, slices=[dict(POLICY["slices"][0], superseded_by=None, slice_id="CANON-COMMISSION-001")])
    repo, sha = release_repo(tmp_path, original)
    state = tmp_path / "ExponentialEngine" / "flow-state"
    engine = SimpleNamespace(repo=repo, sha=sha, state=state, memory=tmp_path / "shared-memory", tmp=tmp_path,
                             supervisor=grant_owner_authority(state, release_sha=sha).as_dict(),
                             pool=RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES, tmp_path / "runtime-leases.json"))
    step(engine, CrashingRunner({}))
    assert step(engine, CrashingRunner({}))["status"] == "PARKED"
    replanned = dict(POLICY, slices=[dict(POLICY["slices"][0], slice_id="CANON-COMMISSION-001",
                                          superseded_by="CANON-COMMISSION-REPLAN"),
                                     dict(ACTIVE, slice_id="CANON-COMMISSION-REPLAN")])
    (repo / "engineering" / "iron-god-commissioning.json").write_text(json.dumps(replanned), encoding="utf-8")
    new_release(engine)
    good = Runner({"src/project_orchestrator/canon_ledger_summary.py": GOOD_SOURCE,
                   "tests/test_canon_ledger_summary.py": GOOD_TEST})
    dispatched = step(engine, good)
    assert dispatched["status"] == "DISPATCHED" and dispatched["packet_id"] == "CANON-COMMISSION-REPLAN"
    old = ledger(engine)["packets"]["CANON-COMMISSION-001"]
    assert old["state"] == "CANCELLED" and old["attempt_count"] == 1  # never retried
    cancel = [e for e in ledger(engine)["events"]
              if e["packet_id"] == "CANON-COMMISSION-001" and e.get("state_after") == "CANCELLED"]
    assert [e.get("evidence_ref") for e in cancel] == ["policy:superseded-by:CANON-COMMISSION-REPLAN"]
    assert step(engine, good)["status"] == "ACCEPTED_LOCAL"
    assert step(engine, good)["status"] == "IDLE_NO_READY_SLICE"


def test_real_policy_replans_the_first_slice_with_an_unambiguous_ledger_shape():
    first, active = POLICY["slices"][0], ACTIVE
    assert first["superseded_by"] == SID and SID != first["slice_id"]
    assert "dict mapping packet id" in active["objective"]
    assert active["supervisor_acceptance_test"] == first["supervisor_acceptance_test"]


def test_worker_message_names_no_other_file_and_carries_no_memory_text(engine):
    packet = lane_mod.build_packet(ACTIVE, POLICY, base_sha=engine.sha, worktree=engine.tmp / "wt")
    packet["memory_context_text"] = "lesson mentioning AGENTS.md and DoWork.txt"
    message = WORKER.worker_message(packet)
    for forbidden in ACTIVE["forbidden_paths"]:
        assert forbidden.rstrip("/") not in message  # aider would add any named file to its chat
    assert "lesson" not in message and len(message) < 2500
    for allowed in ACTIVE["allowed_paths"]:
        assert allowed in message


def test_lean_aider_invocation_disables_repo_map_and_lists_only_target_files():
    argv, env = WORKER.lean_aider_invocation(lambda message: (["aider", "--message", message], {"X": "1"}),
                                             "do it", ["a.py", "tests/test_a.py"])
    assert argv[-2:] == ["a.py", "tests/test_a.py"]
    flags = argv[:-2]
    assert flags[flags.index("--map-tokens") + 1] == "0" and flags[flags.index("--edit-format") + 1] == "whole"
    assert env == {"X": "1"}


def test_verification_runs_pytest_with_a_private_temp_dir(tmp_path):
    worktree = tmp_path / "wt"
    (worktree / "tests").mkdir(parents=True)
    (worktree / "tests" / "test_tmp.py").write_text(
        "import os\n\n\ndef test_tmp(tmp_path):\n    assert os.environ['TEMP'].endswith('wt.tmp')\n", encoding="utf-8")
    results = WORKER.run_verification(worktree, ["python -m pytest -q -p no:cacheprovider tests/test_tmp.py"])
    assert results[0]["ok"], results[0]["output_tail"]
    assert (tmp_path / "wt.tmp").is_dir() and not (worktree / ".tmp").exists()
