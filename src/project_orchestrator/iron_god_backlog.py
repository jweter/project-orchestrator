"""Roadmap-driven development backlog for the product rotation.

Measured on 2026-10-27: asked to invent its own slices, the local 7B model
proposed the same disconnected text helpers until ~64% of turns were rejected
as repeats, and the one merge was a helper nothing calls. Work now comes from
``engineering/state/iron-god-backlog.json``: slices written by a stronger planner
from each repository's own roadmap, reviewed through this repository's
preflight and owner-held install, and built by the local worker exactly as
written.

An item is dispatched only when it is ``approved``, its level is within the
measured ``development_level`` of the rotation policy, every item it depends on
is done, and it has not exhausted its attempts. Levels follow the owner's
progression: 1 small new function, 2 existing-module change, 3 feature
integration, 4 multi-file feature, 5 complex development. Level 1 is built
from an exact build spec; level 2 only as a directed edit (named units in one
existing module, one behavioural requirement, approved tests that fail on main),
the form measured at 9/12 first try in the 2026-10-28 calibration. Higher levels
stay in the backlog for a stronger implementer or a later calibration.

Ownership (2026-10-29): Iron God shares these repositories with other agents,
and on 2026-10-28 another agent built KEC-6 while it was approved here. Every
approved item names its ``assignee``; the engine dispatches only items assigned
to ``iron-god``, and an approved item assigned to anyone else reserves the
files it edits, so no Iron God item touching those files is dispatched until it
is done or retired. Open pull requests still reserve their files separately.
From the installed release, ``python -m project_orchestrator.iron_god_backlog``
with ``src`` on PYTHONPATH prints the ownership table
(assignee, files, units, status, dependencies, active reservations and
evidence) for any agent to read before changing shared code.

External completion (2026-10-29): KEC-6 merged elsewhere while KEC-7 waited on
it. A directed item may record ``approved_base``, the main commit where its approved
tests were verified to fail. When the worker finds those tests passing on a later
main that changed the module since then, it records ``COMPLETED_EXTERNALLY`` with the
commits as evidence, and dependents proceed; an id in a PR title is never enough. A
file under an open PR blocks the turn (``BLOCKED_CONFLICT``) without using an attempt.

The backlog grants no execution: dispatch still requires the owner controller.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .grant_lifecycle import acquire_lifecycle_lock, release_lifecycle_lock
from .state_io import atomic_json

BACKLOG_PATH = Path("engineering") / "state" / "iron-god-backlog.json"
STATUSES = {"proposed", "approved", "done", "retired"}
REQUIRED_TEXT = ("id", "repository", "title", "roadmap_ref", "addresses", "why", "consumer", "objective",
                 "verification", "definition_of_done", "complexity")
REQUIRED_LISTS = ("interfaces", "depends_on", "acceptance_criteria", "tests")
BUILD_FIELDS = ("module_path", "function_name", "signature", "objective", "examples")
DIRECT_FIELDS = ("module_path", "targets", "direction", "test_path", "test_source")
MAX_ITEM_ATTEMPTS = 3
MAX_TEXT = 1100  # Canon bounds handoff text at 1200 characters; the packet adds an id prefix
BUILDABLE_LEVELS = {1, 2}  # the levels the local product worker can build (measured)
DIRECT_ATTEMPTS = 1  # measured: a repeated directed edit re-emits the same answer; a failure goes back to planning
AGENT_ID = "iron-god"  # the assignee this engine builds; every other assignee owns its own items
IN_FLIGHT = {"DISPATCHED", "ACCEPTED_LOCAL", "PR_BLOCKED"}  # an Iron God attempt that still holds its files
EXTERNAL = "COMPLETED_EXTERNALLY"  # verified on main by the worker: approved tests pass and the module changed


def _now() -> str:
    return datetime.now(UTC).isoformat()


def item_problems(item: Any) -> list[str]:
    """Why an item may not be dispatched (empty when it is well-formed)."""
    if not isinstance(item, dict):
        return ["ITEM_NOT_AN_OBJECT"]
    problems = [f"MISSING_{name.upper()}" for name in REQUIRED_TEXT
                if not isinstance(item.get(name), str) or not item[name].strip()]
    problems += [f"MISSING_{name.upper()}" for name in REQUIRED_LISTS if not isinstance(item.get(name), list)]
    if item.get("status") not in STATUSES:
        problems.append("STATUS_INVALID")
    level = item.get("level")
    if not isinstance(level, int) or not 1 <= level <= 5:
        problems.append("LEVEL_INVALID")
    if isinstance(item.get("consumer"), str) and len(item["consumer"].strip()) < 12:
        problems.append("CONSUMER_UNIDENTIFIED")  # no disconnected helpers: name who calls it
    texts = [item.get("objective"), *(item.get("acceptance_criteria") or [])]
    if any(isinstance(text, str) and len(text) > MAX_TEXT for text in texts):
        problems.append("TEXT_OVER_CANON_BOUND")
    if item.get("status") == "approved" and level == 1:
        build = item.get("build")
        if not isinstance(build, dict) or any(not build.get(name) for name in BUILD_FIELDS):
            problems.append("APPROVED_ITEM_WITHOUT_BUILD_SPEC")
    if item.get("status") == "approved" and level == 2:
        direct = item.get("direct")
        if not isinstance(direct, dict) or any(not direct.get(name) for name in DIRECT_FIELDS):
            problems.append("APPROVED_ITEM_WITHOUT_DIRECTION")
    if item.get("status") == "approved" and (not isinstance(item.get("assignee"), str) or not item["assignee"].strip()):
        problems.append("APPROVED_ITEM_WITHOUT_ASSIGNEE")
    return problems


def edit_files(item: dict[str, Any]) -> set[str]:
    """Every file an item may change: its interfaces plus its build or direct module and test."""
    files = {str(path) for path in item.get("interfaces") or []}
    for spec in (item.get("build"), item.get("direct")):
        if isinstance(spec, dict):
            files |= {str(spec[key]) for key in ("module_path", "test_path") if spec.get(key)}
    return {path.replace("\\", "/") for path in files}


def conflicts(item: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    """Approved items assigned to another developer that reserve a file this item would change."""
    files = edit_files(item)
    return [other["id"] for other in items
            if other["id"] != item["id"] and other["repository"] == item["repository"]
            and other["status"] == "approved" and other.get("assignee") != AGENT_ID and files & edit_files(other)]


def load_backlog(release_repo: Path) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Well-formed items, and the problems of every rejected one (a malformed item is never dispatched)."""
    try:
        raw = json.loads((Path(release_repo) / BACKLOG_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], {"<backlog>": ["BACKLOG_UNREADABLE"]}
    items, rejected, seen = [], {}, set()
    for item in raw.get("items", []) if isinstance(raw, dict) else []:
        problems = item_problems(item)
        key = str(item.get("id")) if isinstance(item, dict) else "<item>"
        if key in seen:
            problems.append("DUPLICATE_ID")
        seen.add(key)
        if problems:
            rejected[key] = problems
        else:
            items.append(item)
    return items, rejected


def state_path(lane_root: Path) -> Path:
    return Path(lane_root) / "backlog-state.json"


def load_state(lane_root: Path) -> dict[str, Any]:
    try:
        return json.loads(state_path(lane_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": 1, "items": {}}


def _record_item_unlocked(lane_root: Path, item_id: str, *, packet_id: str, outcome: str) -> dict[str, Any]:
    """One attempt's outcome; MERGED marks the item done for dependency and dispatch purposes."""
    state = load_state(lane_root)
    row = state.setdefault("items", {}).setdefault(item_id, {"attempts": 0, "packets": []})
    if packet_id not in row["packets"]:
        row["packets"].append(packet_id)
        row["attempts"] = len(row["packets"])
    row.update(outcome=outcome, updated_at_utc=_now())
    if outcome == "MERGED":
        row["merged_at_utc"] = _now()
    atomic_json(state_path(lane_root), state)
    return row


def record_item(lane_root: Path, item_id: str, *, packet_id: str, outcome: str) -> dict[str, Any]:
    """Serialize each backlog outcome read/modify/write across processes."""
    lock = acquire_lifecycle_lock(state_path(lane_root))
    try:
        return _record_item_unlocked(lane_root, item_id, packet_id=packet_id, outcome=outcome)
    finally:
        release_lifecycle_lock(lock)


def _record_block_unlocked(lane_root: Path, item_id: str, *, outcome: str) -> dict[str, Any]:
    """A turn that did not start (e.g. its files are under another developer's open PR): no attempt is used."""
    state = load_state(lane_root)
    row = state.setdefault("items", {}).setdefault(item_id, {"attempts": 0, "packets": []})
    row.update(outcome=outcome, updated_at_utc=_now())
    atomic_json(state_path(lane_root), state)
    return row


def record_block(lane_root: Path, item_id: str, *, outcome: str) -> dict[str, Any]:
    """Serialize non-attempting block outcomes with worker outcomes."""
    lock = acquire_lifecycle_lock(state_path(lane_root))
    try:
        return _record_block_unlocked(lane_root, item_id, outcome=outcome)
    finally:
        release_lifecycle_lock(lock)


def is_done(item: dict[str, Any], state: dict[str, Any]) -> bool:
    outcome = str(state.get("items", {}).get(item["id"], {}).get("outcome", ""))
    return item.get("status") == "done" or outcome == "MERGED" or outcome.startswith(EXTERNAL)


def slice_state(item: dict[str, Any], row: dict[str, Any], *, done: set[str], conflicting: list[str]) -> str:
    """The item's semantic state, derived from the backlog and the lane record (never stored twice)."""
    outcome = str(row.get("outcome", ""))
    if outcome.startswith(EXTERNAL) or (item.get("completion") or {}).get("kind") == EXTERNAL:
        return EXTERNAL
    if item["status"] == "done" or outcome == "MERGED":
        return "COMPLETED"
    if item["status"] == "retired":
        return "SUPERSEDED" if item.get("superseded_by") else "RETIRED"
    if item["status"] == "proposed":
        return "PROPOSED"
    if item.get("assignee") != AGENT_ID:
        return "ASSIGNED_ELSEWHERE"
    if conflicting or outcome.startswith("BLOCKED_CONFLICT"):
        return "BLOCKED_CONFLICT"
    if outcome in IN_FLIGHT:
        return "IN_FLIGHT"
    if "NEEDS_REVALIDATION" in outcome:
        return "NEEDS_REVALIDATION"
    if outcome.startswith(("FAIL", "REVIEW_REQUIRED", "SUPERVISOR_RECHECK_FAILED")):
        return "FAILED_IMPLEMENTATION"
    if not all(dep in done for dep in item["depends_on"]):
        return "BLOCKED_DEPENDENCY"
    return "READY"


def next_item(items: list[dict[str, Any]], state: dict[str, Any], repo: str, *,
              max_level: int) -> dict[str, Any] | None:
    """The first dispatchable item for ``repo`` in backlog order, or None."""
    done = {item["id"] for item in items if is_done(item, state)}
    for item in items:
        row = state.get("items", {}).get(item["id"], {})
        if (item["repository"] == repo and item["status"] == "approved" and item["level"] <= max_level
                and item.get("assignee") == AGENT_ID and not conflicts(item, items)
                and item["level"] in BUILDABLE_LEVELS and item["id"] not in done
                and row.get("attempts", 0) < MAX_ITEM_ATTEMPTS
                # a directed slice that the model failed is not retried: it needs decomposition (an
                # interrupted or environment-failed attempt is not a model failure)
                and not (item.get("direct") and row.get("attempts", 0) >= DIRECT_ATTEMPTS
                         and not str(row.get("outcome", "")).startswith("ENVIRONMENT_FAILURE"))
                # its pull request is in flight, or blocked on GitHub and waiting for the owner
                and row.get("outcome") not in {"ACCEPTED_LOCAL", "PR_BLOCKED"}
                and all(dep in done for dep in item["depends_on"])):
            return item
    return None


def ownership(items: list[dict[str, Any]], state: dict[str, Any], *, repository: str | None = None,
              path: str | None = None) -> list[dict[str, Any]]:
    """Who owns what: one row per item, for any agent to read before changing shared code (read-only)."""
    rows = []
    done = {item["id"] for item in items if is_done(item, state)}
    for item in items:
        files = edit_files(item)
        if (repository and item["repository"] != repository) or (path and path.replace("\\", "/") not in files):
            continue
        row = state.get("items", {}).get(item["id"], {})
        reservation = None
        if item.get("assignee") == AGENT_ID and row.get("outcome") in IN_FLIGHT:
            reservation = {"by": AGENT_ID, "outcome": row["outcome"], "packet_id": (row.get("packets") or [""])[-1],
                           "since_utc": row.get("updated_at_utc")}
        elif item["status"] == "approved" and item.get("assignee") != AGENT_ID:
            reservation = {"by": item.get("assignee"), "outcome": "ASSIGNED"}
        direct = item.get("direct") if isinstance(item.get("direct"), dict) else {}
        build = item.get("build") if isinstance(item.get("build"), dict) else {}
        units = [*direct.get("targets", []), *(a.get("name") for a in direct.get("add", []) if isinstance(a, dict))]
        conflicting = conflicts(item, items)
        rows.append({
            "id": item["id"], "assignee": item.get("assignee"), "repository": item["repository"],
            "status": item["status"], "state": slice_state(item, row, done=done, conflicting=conflicting),
            "files": sorted(files),
            "units": units or ([build["function_name"]] if build.get("function_name") else []),
            "depends_on": item["depends_on"], "reservation": reservation, "conflicts": conflicting,
            "evidence": {key: value for key, value in {
                "completion": item.get("completion"), "retirement": item.get("retirement"),
                "superseded_by": item.get("superseded_by"), "attempts": row.get("attempts"),
                "outcome": row.get("outcome"), "merged_at_utc": row.get("merged_at_utc")}.items() if value},
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the Iron God backlog ownership table (read-only).")
    parser.add_argument("--repository", help="only this repository, e.g. jweter/knowledge-engine-core")
    parser.add_argument("--path", help="only items that may change this repository-relative file")
    parser.add_argument("--release-repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--lane-root", type=Path, default=Path(os.environ.get("LOCALAPPDATA", "."))
                        / "ExponentialEngine" / "flow-state" / "iron-god-products")
    args = parser.parse_args(argv)
    items, rejected = load_backlog(args.release_repo)
    table = ownership(items, load_state(args.lane_root), repository=args.repository, path=args.path)
    json.dump({"agent": AGENT_ID, "items": table, "rejected": rejected}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if not rejected else 1


if __name__ == "__main__":
    raise SystemExit(main())
