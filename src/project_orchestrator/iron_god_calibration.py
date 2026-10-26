"""Owner-held calibration mode: measure what this machine's local worker can build.

Calibration runs only while the owner holds control (the engine is stopped),
no Canon worker is active, and the exclusive local-model lease is free. It
drives the exact production worker path -- the same prompt builder, the same
supervised aider-ollama runner and the same verification -- over a fixed
ladder of calibration slices in a scratch clone of the accepted release. Each
slice is scored by a hidden acceptance test the worker never sees.

The resulting capability profile is bound to the worker model and the
hardware. The commissioning lane refuses to dispatch a slice whose declared
``capability_level`` exceeds the calibrated level, and dispatches nothing
without a matching profile (fail-closed).
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import stat
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from . import iron_god_owner_control as owner_controller
from .state_io import atomic_json

PROFILE_NAME = "iron-god-capability-profile.json"
LADDER_VERSION = 1
CALIBRATION_LEASE_ID = "iron-god-calibration"
# Same default as scripts/autonomous_slice_builder.MODEL (asserted by tests).
DEFAULT_MODEL = os.environ.get("EXPONENTIAL_BUILDER_MODEL", "ollama/qwen2.5-coder:7b")

_PLANNING = {
    "vision_ref": "calibration ladder (owner-held capability measurement)",
    "roadmap_ref": "docs/IRON_GOD_OPERATIONAL_RECOVERY_20261025.md (capability ladder)",
    "architecture_ref": "src/project_orchestrator/iron_god_calibration.py",
    "current_state_ref": "scratch clone of the accepted release",
    "dependency_evidence_ref": "none; calibration slices are independent",
}
_LEDGER_SHAPE = (
    "ledger is a dict; ledger['packets'] is a dict mapping packet id -> packet dict, and ledger['events'] is a "
    "list of event dicts. Return a new dict with exactly these keys: packet_count (number of packets), "
    "state_counts (dict mapping each packet's 'state' to how many packets have it, inserted in sorted key order), "
    "accepted_refs (sorted list of every packet's 'accepted_ref' value that is present and non-empty), "
    "last_event_type (the 'type' of the last event, or None when there are no events). Missing 'packets' or "
    "'events' keys count as empty. Do not modify the input."
)
_LEDGER_CRITERIA = [
    "summarize_ledger({}) returns packet_count 0, state_counts {}, accepted_refs [], last_event_type None",
    "state_counts counts packets per state with sorted keys",
    "accepted_refs is the sorted list of non-empty accepted_ref values",
    "last_event_type is the type of the final event",
    "the input ledger is not modified",
]
_LEDGER_HIDDEN = '''from copy import deepcopy

from project_orchestrator.calibration_ledger_summary import summarize_ledger


def test_hidden_empty():
    assert summarize_ledger({}) == {'packet_count': 0, 'state_counts': {}, 'accepted_refs': [], 'last_event_type': None}


def test_hidden_counts():
    ledger = {'packets': {'p2': {'state': 'B', 'accepted_ref': 'r2'}, 'p1': {'state': 'A', 'accepted_ref': ''},
                          'p3': {'state': 'B', 'accepted_ref': 'r1'}},
              'events': [{'type': 'X'}, {'type': 'Y'}]}
    before = deepcopy(ledger)
    summary = summarize_ledger(ledger)
    assert ledger == before
    assert summary == {'packet_count': 3, 'state_counts': {'A': 1, 'B': 2}, 'accepted_refs': ['r1', 'r2'],
                       'last_event_type': 'Y'}
    assert list(summary['state_counts']) == ['A', 'B']
'''

# Level N is the largest slice shape the worker passes reliably. Rungs are
# written like real commissioning slices: every required behaviour is also an
# explicit acceptance criterion (measured: the local 7B misses a requirement
# stated only once in prose). The ladder is versioned: changing a rung changes
# LADDER_VERSION, which invalidates profiles.
LADDER: tuple[dict[str, Any], ...] = (
    {
        "level": 1, "slice_id": "CALIBRATION-L1-FUNCTION",
        "objective": "Add src/project_orchestrator/calibration_slug.py defining slugify(text: str) -> str. It "
                     "lowercases the text, replaces every run of characters that are not a-z or 0-9 with a single "
                     "hyphen, and strips leading and trailing hyphens. slugify('') returns ''. Use only the standard library.",
        "acceptance_criteria": ["slugify('Hello, World!') returns 'hello-world'",
                                "runs of other characters become one hyphen; no leading or trailing hyphen",
                                "slugify('') returns ''"],
        "allowed_paths": ["src/project_orchestrator/calibration_slug.py"],
        "verification_commands": ["python -m ruff check src/project_orchestrator/calibration_slug.py"],
        "hidden_test": "from project_orchestrator.calibration_slug import slugify\n\n\n"
                       "def test_hidden():\n"
                       "    assert slugify('Hello, World!') == 'hello-world'\n"
                       "    assert slugify('  A--B  ') == 'a-b'\n"
                       "    assert slugify('') == ''\n"
                       "    assert slugify('Iron God 2026') == 'iron-god-2026'\n",
    },
    {
        "level": 2, "slice_id": "CALIBRATION-L2-SPECIFIED-FUNCTION",
        "objective": "Add src/project_orchestrator/calibration_ledger_summary.py defining summarize_ledger(ledger). "
                     + _LEDGER_SHAPE + " Use only the standard library.",
        "acceptance_criteria": _LEDGER_CRITERIA,
        "allowed_paths": ["src/project_orchestrator/calibration_ledger_summary.py"],
        "verification_commands": ["python -m ruff check src/project_orchestrator/calibration_ledger_summary.py"],
        "hidden_test": _LEDGER_HIDDEN,
    },
    {
        "level": 3, "slice_id": "CALIBRATION-L3-FUNCTION-AND-TESTS",
        "objective": "Add src/project_orchestrator/calibration_ledger_summary.py defining summarize_ledger(ledger). "
                     + _LEDGER_SHAPE + " Also add tests/test_calibration_ledger_summary.py with pytest tests for an "
                     "empty ledger and for a ledger with two packets in different states. Use only the standard "
                     "library and pytest.",
        "acceptance_criteria": [*_LEDGER_CRITERIA, "the new tests pass and ruff reports no issues"],
        "allowed_paths": ["src/project_orchestrator/calibration_ledger_summary.py",
                          "tests/test_calibration_ledger_summary.py"],
        "verification_commands": [
            "python -m pytest -q -p no:cacheprovider tests/test_calibration_ledger_summary.py",
            "python -m ruff check src/project_orchestrator/calibration_ledger_summary.py "
            "tests/test_calibration_ledger_summary.py",
        ],
        "hidden_test": _LEDGER_HIDDEN,
    },
)


class CalibrationRefused(RuntimeError):
    """Calibration may not run in the current machine state."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def remove_tree(path: Path) -> None:
    """Delete a scratch tree; Git object files are read-only on Windows."""
    def clear_readonly(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    if path.exists():
        shutil.rmtree(path, onexc=clear_readonly)


def profile_path(state_root: Path) -> Path:
    return Path(state_root) / PROFILE_NAME


def hardware_fingerprint() -> dict[str, Any]:
    ram_gib = None
    if os.name == "nt":
        class _Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong),
                        ("avail_page", ctypes.c_ulonglong), ("total_virtual", ctypes.c_ulonglong),
                        ("avail_virtual", ctypes.c_ulonglong), ("avail_extended", ctypes.c_ulonglong)]
        status = _Status()
        status.length = ctypes.sizeof(_Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            ram_gib = round(status.total / 1024**3)
    return {"processor": platform.processor(), "logical_cpus": os.cpu_count(), "ram_gib": ram_gib}


def calibration_spec(rung: dict[str, Any]) -> dict[str, Any]:
    """A rung as a full Canon slice spec, so the worker sees a production-shaped packet."""
    return {
        "slice_id": rung["slice_id"],
        "depends_on": [],
        "objective": rung["objective"],
        "acceptance_criteria": [*rung["acceptance_criteria"], "the listed verification commands pass"],
        "allowed_paths": list(rung["allowed_paths"]),
        "forbidden_paths": [],
        "verification_commands": list(rung["verification_commands"]),
        "expected_artifacts": list(rung["allowed_paths"]),
        "run_budget_minutes": 15,
        "planning_refs": dict(_PLANNING),
    }


def summarize(levels: dict[str, dict[str, Any]]) -> tuple[int, int | None]:
    """Highest contiguous level passing every run, and a worker timeout sized from it."""
    max_level = 0
    for rung in LADDER:
        row = levels.get(str(rung["level"]))
        if not row or row["pass_rate"] < 1.0:
            break
        max_level = rung["level"]
    walls = [levels[str(level)]["median_wall_s"] for level in range(1, max_level + 1)]
    timeout = int(min(2700, max(600, 5 * max(walls)))) if walls else None
    return max_level, timeout


def run_calibration(
    state_root: Path,
    release_repo: Path,
    *,
    build_message: Callable[[dict[str, Any]], str],
    run_agent: Callable[[Path, str, list[str], Callable[[], None]], Any],
    verify: Callable[[Path, list[str]], list[dict[str, Any]]],
    changed_paths: Callable[[Path], list[str]],
    lease_pool: Any,
    runs: int = 2,
    model: str = DEFAULT_MODEL,
    lane_active: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run the ladder and write the capability profile. Raises CalibrationRefused."""
    from .iron_god_canon_lane import WORKER_LEASE_RUNTIME, Lane, build_packet, load_policy

    root = owner_controller.control_root(state_root)

    def check() -> None:
        status = owner_controller.owner_status(root)
        if status != owner_controller.OWNER_CONTROL_ACTIVE:
            raise CalibrationRefused(f"OWNER_MUST_HOLD_CONTROL: {status}")

    check()
    lane = Lane(state_root)
    if (lane_active or (lambda: lane.active() is not None))():
        raise CalibrationRefused("CANON_WORKER_ACTIVE")
    policy = load_policy(release_repo)
    if policy is None:
        raise CalibrationRefused("COMMISSIONING_POLICY_UNAVAILABLE")
    head = subprocess.run(["git", "-C", str(release_repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=False, timeout=60).stdout.strip()
    try:
        lease = lease_pool.acquire(WORKER_LEASE_RUNTIME, CALIBRATION_LEASE_ID, owner_pid=os.getpid())
    except Exception as exc:  # Never share the local model with a worker or builder.
        raise CalibrationRefused(f"MODEL_LEASE_BUSY: {type(exc).__name__}") from exc
    work = lane.workspaces / "calibration"
    clone = work / "clone"
    levels: dict[str, dict[str, Any]] = {}
    try:
        remove_tree(clone)
        remove_tree(work / "evidence")  # evidence describes only the latest calibration
        work.mkdir(parents=True, exist_ok=True)
        cloned = subprocess.run(["git", "clone", "--quiet", "--no-local", str(release_repo), str(clone)],
                                capture_output=True, text=True, check=False, timeout=900)
        if cloned.returncode != 0:
            raise CalibrationRefused("CALIBRATION_CLONE_FAILED: " + cloned.stderr[-300:])
        failed_below = False
        for rung in LADDER:
            key = str(rung["level"])
            if failed_below:
                levels[key] = {"slice_id": rung["slice_id"], "runs": [], "pass_rate": 0.0,
                               "median_wall_s": None, "status": "SKIPPED_LOWER_LEVEL_FAILED"}
                continue
            results = [_run_rung(rung, i, clone, policy, head, check, build_message, run_agent, verify,
                                 changed_paths, build_packet) for i in range(runs)]
            passed = [r for r in results if r["ok"]]
            levels[key] = {
                "slice_id": rung["slice_id"], "runs": results,
                "pass_rate": round(len(passed) / len(results), 3),
                "median_wall_s": round(statistics.median(r["wall_s"] for r in results), 1),
                "status": "MEASURED",
            }
            failed_below = len(passed) < len(results)
    finally:
        lease_pool.release(CALIBRATION_LEASE_ID, lease.lease_id)
        remove_tree(clone)
    max_level, timeout = summarize(levels)
    profile = {
        "schema_version": 1, "ladder_version": LADDER_VERSION, "model": model,
        "hardware": hardware_fingerprint(), "release_sha": head, "runs_per_level": runs,
        "calibrated_at_utc": _now(), "levels": levels,
        "max_passing_level": max_level, "recommended_worker_timeout_s": timeout,
    }
    atomic_json(profile_path(state_root), profile)
    return profile


def _run_rung(rung, index, clone, policy, head, check, build_message, run_agent, verify, changed_paths,
              build_packet) -> dict[str, Any]:
    check()
    worktree = clone.parent / "runs" / f"L{rung['level']}-{index}"
    subprocess.run(["git", "-C", str(clone), "worktree", "remove", "--force", str(worktree)],
                   capture_output=True, text=True, check=False, timeout=120)
    remove_tree(worktree)
    added = subprocess.run(["git", "-C", str(clone), "worktree", "add", "--detach", str(worktree), head],
                           capture_output=True, text=True, check=False, timeout=300)
    if added.returncode != 0:
        return {"ok": False, "wall_s": 0.0, "detail": "WORKTREE_FAILED: " + added.stderr[-200:]}
    spec = calibration_spec(rung)
    packet = build_packet(spec, policy, base_sha=head, worktree=worktree)
    started = time.monotonic()
    try:
        run_agent(worktree, build_message(packet), packet["paths"], check)
        agent = "OK"
    except CalibrationRefused:
        raise
    except Exception as exc:
        agent = f"AGENT_FAILED: {type(exc).__name__}: {str(exc)[-160:]}"
    wall = round(time.monotonic() - started, 1)
    try:
        # Scope is what the agent changed, captured before verification writes caches.
        changed = [p for p in changed_paths(worktree) if not p.startswith(".aider")]
        outside = [p for p in changed if p not in rung["allowed_paths"]]
        own = verify(worktree, list(rung["verification_commands"]))
        hidden = worktree / "tests" / "test_zz_calibration_hidden.py"
        hidden.parent.mkdir(parents=True, exist_ok=True)
        hidden.write_text(rung["hidden_test"], encoding="utf-8")
        hid = verify(worktree, ["python -m pytest -q -p no:cacheprovider tests/test_zz_calibration_hidden.py"])
        hidden.unlink()
        ok = agent == "OK" and all(c["ok"] for c in own) and hid[0]["ok"] and bool(changed) and not outside
        detail = agent if agent != "OK" else ("PASS" if ok else json.dumps({
            "own": [c["ok"] for c in own], "hidden": hid[0]["ok"], "outside": outside, "changed": changed}))
        result = {"ok": ok, "wall_s": wall, "detail": detail[:400]}
        if not ok:  # Keep what the worker produced so a failed level can be diagnosed.
            evidence = clone.parent / "evidence" / worktree.name
            for path in (p for p in changed if p in rung["allowed_paths"]):
                target = evidence / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((worktree / path).read_bytes()[:65536])
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "verification.json").write_text(json.dumps({"own": own, "hidden": hid}, indent=2),
                                                        encoding="utf-8")
            result["evidence"] = str(evidence)
        return result
    finally:
        subprocess.run(["git", "-C", str(clone), "worktree", "remove", "--force", str(worktree)],
                       capture_output=True, text=True, check=False, timeout=120)
        remove_tree(worktree)


def load_profile(state_root: Path) -> dict[str, Any] | None:
    try:
        return json.loads(profile_path(state_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def capability_gate(profile: dict[str, Any] | None, spec: dict[str, Any], *, model: str = DEFAULT_MODEL,
                    hardware: dict[str, Any] | None = None) -> str | None:
    """None when this machine is calibrated for the slice; otherwise the fail-closed reason."""
    level = spec.get("capability_level")
    if type(level) is not int or level < 1:
        return "SLICE_CAPABILITY_LEVEL_UNDECLARED"
    if not isinstance(profile, dict) or profile.get("schema_version") != 1 \
            or profile.get("ladder_version") != LADDER_VERSION:
        return "CALIBRATION_REQUIRED"
    if profile.get("model") != model:
        return "CALIBRATION_STALE_MODEL"
    current = hardware if hardware is not None else hardware_fingerprint()
    if profile.get("hardware") != current:
        return "CALIBRATION_STALE_HARDWARE"
    if level > int(profile.get("max_passing_level") or 0):
        return "SLICE_EXCEEDS_CALIBRATED_CAPABILITY"
    return None
