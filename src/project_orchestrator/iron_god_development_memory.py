"""Development memory: what each Iron God product attempt did, and planning rules learned from it.

The 2026-10-28/29 pilot failed three directed slices for reasons a planner could
have prevented (an unannotated nested class under strict mypy, a helper moved to
module level, a lone dict entry instead of the whole assignment). This local
SQLite store keeps one row of observed facts per attempt -- slice, repository,
exact base, model, method, instructions, prompt, reply, test and static-analysis
results, failure class and outcome -- and derives planning rules from failure
classes seen on at least two different slices.

Facts and inferences are kept apart: ``attempts`` holds only what the worker
recorded; ``rules`` holds catalogued recommendations, each citing the attempts
behind it. Rules are read by planners preparing slices (``rules`` command); they
never change prompts, grants, scope or gates, and nothing here trains a model.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_NAME = "development-memory.sqlite3"
SCHEMA_VERSION = 1
MIN_INDEPENDENT_SLICES = 2  # a rule needs the same failure class on this many different slices

# Failure class -> the planning rule it supports (reviewed text; never generated).
RULE_CATALOG = {
    "STATIC_MYPY_UNTYPED": "In strict-mypy repositories, the direction must require complete annotations of every "
                           "introduced function, method and nested helper (including -> None), and typed new "
                           "attributes.",
    "STATIC_LINE_LENGTH": "State the repository's line-length limit and how to split long strings in the direction.",
    "REPLY_UNEXPECTED_UNIT": "Say where helpers go: inside the target unit, or as an authorized `add` with an exact "
                             "name; the model otherwise hoists helpers to module level.",
    "REPLY_MALFORMED_BLOCK": "For an assignment target, require the whole statement with every entry; the model "
                             "otherwise returns only the entry it changed.",
    "REPLY_MISSING_UNITS": "Name every unit the change needs, including imports, and keep combined edits to one "
                           "behaviour.",
    "SLICE_TESTS_FAILED": "Re-check the behavioural requirement and reference fix; split the slice if more than one "
                          "behaviour is involved.",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def db_path(lane_root: Path) -> Path:
    return Path(lane_root) / DB_NAME


def _connect(lane_root: Path) -> sqlite3.Connection:
    path = db_path(lane_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (
            packet_id TEXT PRIMARY KEY, item_id TEXT, repository TEXT NOT NULL, base_sha TEXT NOT NULL,
            module_path TEXT, targets TEXT NOT NULL, method TEXT NOT NULL, model TEXT, instructions TEXT,
            prompt TEXT, reply TEXT, tests TEXT, static TEXT, failure_class TEXT NOT NULL,
            outcome TEXT NOT NULL, recorded_at_utc TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rules (
            failure_class TEXT PRIMARY KEY, rule TEXT NOT NULL, evidence TEXT NOT NULL,
            slices INTEGER NOT NULL, kind TEXT NOT NULL, derived_at_utc TEXT NOT NULL);
    """)
    connection.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    return connection


def classify(status: str, summary: str, directed: dict[str, Any]) -> str:
    """A stable failure class from the worker's own result (observed, not inferred)."""
    text = f"{summary}\n{directed.get('apply_error', '')}"
    failure = str(directed.get("test_failure", ""))
    if status == "PASS":
        return "PASSED"
    for marker, name in (("COMPLETED_EXTERNALLY", "COMPLETED_EXTERNALLY"), ("BLOCKED_CONFLICT", "BLOCKED_CONFLICT"),
                         ("UNDER_REVIEW", "BLOCKED_CONFLICT"), ("NOT_DISCRIMINATING", "TEST_NOT_DISCRIMINATING"),
                         ("NEEDS_REVALIDATION", "NEEDS_REVALIDATION"), ("UNEXPECTED_UNIT", "REPLY_UNEXPECTED_UNIT"),
                         ("DUPLICATE_UNIT", "REPLY_DUPLICATE_UNIT"), ("MALFORMED_BLOCK", "REPLY_MALFORMED_BLOCK"),
                         ("MISSING_UNITS", "REPLY_MISSING_UNITS"), ("NO_CODE_BLOCKS", "REPLY_NO_CODE"),
                         ("UNNAMED_CODE", "REPLY_UNNAMED_CODE")):
        if marker in text:
            return name
    if "SLICE_TESTS_FAILED" in text:
        if "no-untyped" in failure or "missing a return type" in failure or "missing a type annotation" in failure:
            return "STATIC_MYPY_UNTYPED"
        if "E501" in failure:
            return "STATIC_LINE_LENGTH"
        if ": error:" in failure:
            return "STATIC_MYPY"
        return "SLICE_TESTS_FAILED"
    if status == "ENVIRONMENT_FAILURE":
        return "ENVIRONMENT"
    return "OTHER"


def record_attempt(lane_root: Path, packet: dict[str, Any], handback: dict[str, Any], outcome: str) -> str:
    """One attempt's observed facts (idempotent per packet); returns its failure class."""
    # a review repair is recorded against the slice whose pull request it repairs
    item = packet.get("slice") or (packet.get("repair") or {}).get("slice") or {}
    plan = handback.get("plan") or {}
    directed = handback.get("directed") or {}
    failure_class = classify(str(handback.get("status", "")), str(handback.get("summary", "")), directed)
    instructions = {"direction": plan.get("direction"), "objective": plan.get("objective"),
                    "add": plan.get("add"), "test_path": plan.get("test_path")}
    with closing(_connect(lane_root)) as connection, connection:
        connection.execute(
            "INSERT OR REPLACE INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (packet["packet_id"], item.get("id"), packet["repository"], packet["base_sha"], plan.get("module_path"),
             json.dumps(plan.get("targets") or []), plan.get("mode") or ("build" if plan else "none"),
             directed.get("model"), json.dumps(instructions), directed.get("prompt"), directed.get("reply"),
             json.dumps(handback.get("verification") or []), directed.get("test_failure"), failure_class,
             outcome[:300], _now()))
    return failure_class


def derive_rules(lane_root: Path) -> list[dict[str, Any]]:
    """Rules for failure classes seen on at least MIN_INDEPENDENT_SLICES different slices, with evidence."""
    with closing(_connect(lane_root)) as connection, connection:
        rows = connection.execute("SELECT failure_class, item_id, packet_id FROM attempts "
                                  "WHERE item_id IS NOT NULL ORDER BY recorded_at_utc").fetchall()
        groups: dict[str, dict[str, list[str]]] = {}
        for row in rows:
            groups.setdefault(row["failure_class"], {}).setdefault(row["item_id"], []).append(row["packet_id"])
        derived = []
        for failure_class, slices in sorted(groups.items()):
            if failure_class not in RULE_CATALOG or len(slices) < MIN_INDEPENDENT_SLICES:
                continue
            evidence = {item: packets for item, packets in sorted(slices.items())}
            connection.execute("INSERT OR REPLACE INTO rules VALUES (?,?,?,?,?,?)",
                               (failure_class, RULE_CATALOG[failure_class], json.dumps(evidence), len(slices),
                                "INFERRED_RECOMMENDATION", _now()))
            derived.append({"failure_class": failure_class, "rule": RULE_CATALOG[failure_class],
                            "evidence": evidence, "slices": len(slices), "kind": "INFERRED_RECOMMENDATION"})
    return derived


def rules_for(lane_root: Path, repository: str | None = None) -> list[dict[str, Any]]:
    """Derived rules, each with the repositories its evidence came from (most-evidenced first)."""
    derive_rules(lane_root)
    with closing(_connect(lane_root)) as connection:
        repos = {row["packet_id"]: row["repository"]
                 for row in connection.execute("SELECT packet_id, repository FROM attempts")}
        found = []
        for row in connection.execute("SELECT * FROM rules ORDER BY slices DESC, failure_class"):
            evidence = json.loads(row["evidence"])
            seen = sorted({repos[p] for packets in evidence.values() for p in packets if p in repos})
            if repository is None or repository in seen:
                found.append({"failure_class": row["failure_class"], "rule": row["rule"], "kind": row["kind"],
                              "slices": row["slices"], "evidence": evidence, "repositories": seen})
    return found


def stats(lane_root: Path) -> dict[str, Any]:
    """First-attempt acceptance: how often a slice's first attempt was accepted locally (the measured rate)."""
    with closing(_connect(lane_root)) as connection:
        rows = connection.execute("SELECT item_id, failure_class, outcome FROM attempts WHERE item_id IS NOT NULL "
                                  "ORDER BY recorded_at_utc").fetchall()
    first: dict[str, Any] = {}
    classes: dict[str, int] = {}
    for row in rows:
        first.setdefault(row["item_id"], row)
        classes[row["failure_class"]] = classes.get(row["failure_class"], 0) + 1
    judged = [r for r in first.values() if r["failure_class"] not in
              {"COMPLETED_EXTERNALLY", "BLOCKED_CONFLICT", "ENVIRONMENT", "TEST_NOT_DISCRIMINATING"}]
    accepted = sum(1 for r in judged if r["failure_class"] == "PASSED")
    return {"attempts": len(rows), "slices": len(first), "first_attempt_judged": len(judged),
            "first_attempt_accepted": accepted,
            "first_attempt_rate": round(accepted / len(judged), 3) if judged else None, "failure_classes": classes}


def backfill(lane_root: Path) -> int:
    """Record every harvested attempt already on disk (packets, handback diagnostics, backlog outcomes)."""
    lane_root = Path(lane_root)
    try:
        outcomes = json.loads((lane_root / "backlog-state.json").read_text(encoding="utf-8")).get("items", {})
    except (OSError, ValueError):
        outcomes = {}
    by_packet = {packet: row.get("outcome", "") for row in outcomes.values() for packet in row.get("packets", [])}
    count = 0
    for packet_path in sorted((lane_root / "packets").glob("*.json")):
        try:
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            handback = json.loads((lane_root / "handbacks" / packet_path.name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            diagnostics = json.loads((lane_root / "reports" / f"{packet_path.stem}-handback-diagnostics.json")
                                     .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            diagnostics = {}
        record_attempt(lane_root, packet, {**diagnostics, **handback},
                       str(by_packet.get(packet["packet_id"]) or handback.get("status", "")))
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Iron God development memory (read-mostly).")
    parser.add_argument("command", choices=("rules", "stats", "backfill"))
    parser.add_argument("--repository")
    parser.add_argument("--lane-root", type=Path, default=Path(os.environ.get("LOCALAPPDATA", "."))
                        / "ExponentialEngine" / "flow-state" / "iron-god-products")
    args = parser.parse_args(argv)
    if args.command == "backfill":
        result: Any = {"recorded": backfill(args.lane_root)}
    elif args.command == "stats":
        result = stats(args.lane_root)
    else:
        result = {"rules": rules_for(args.lane_root, args.repository)}
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
