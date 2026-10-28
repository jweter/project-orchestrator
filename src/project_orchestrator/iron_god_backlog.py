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

The backlog grants no execution: dispatch still requires the owner controller.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    return problems


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


def record_item(lane_root: Path, item_id: str, *, packet_id: str, outcome: str,
                failed_source_fingerprint: str | None = None) -> dict[str, Any]:
    """One attempt's outcome; MERGED marks the item done for dependency and dispatch purposes."""
    state = load_state(lane_root)
    row = state.setdefault("items", {}).setdefault(item_id, {"attempts": 0, "packets": []})
    if packet_id not in row["packets"]:
        row["packets"].append(packet_id)
        row["attempts"] = len(row["packets"])
    if failed_source_fingerprint and outcome not in {"MERGED", "ACCEPTED_LOCAL", "DISPATCHED"}:
        # Store fingerprints only for failed attempts. A repeated source is a
        # planning signal, never permission to extend the existing retry cap.
        previous = row.setdefault("failed_source_fingerprints", [])
        row["repeated_failed_source"] = failed_source_fingerprint in previous
        if failed_source_fingerprint not in previous:
            previous.append(failed_source_fingerprint)
    row.update(outcome=outcome, updated_at_utc=_now())
    if outcome == "MERGED":
        row["merged_at_utc"] = _now()
    atomic_json(state_path(lane_root), state)
    return row


def is_done(item: dict[str, Any], state: dict[str, Any]) -> bool:
    return item.get("status") == "done" or state.get("items", {}).get(item["id"], {}).get("outcome") == "MERGED"


def next_item(items: list[dict[str, Any]], state: dict[str, Any], repo: str, *,
              max_level: int) -> dict[str, Any] | None:
    """The first dispatchable item for ``repo`` in backlog order, or None."""
    done = {item["id"] for item in items if is_done(item, state)}
    for item in items:
        row = state.get("items", {}).get(item["id"], {})
        if (item["repository"] == repo and item["status"] == "approved" and item["level"] <= max_level
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
