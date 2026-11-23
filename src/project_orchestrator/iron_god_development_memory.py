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
SCHEMA_VERSION = 2
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


_TABLE_COLUMNS = {
    "meta": ("key", "value"),
    "attempts": (
        "packet_id", "item_id", "repository", "base_sha", "module_path",
        "targets", "method", "model", "instructions", "prompt", "reply",
        "tests", "static", "failure_class", "outcome", "recorded_at_utc",
    ),
    "rules": ("failure_class", "rule", "evidence", "slices", "kind", "derived_at_utc"),
    "strategy_exposures": ("packet_id", "strategy", "revision", "evidence_packet"),
}


def _connect(lane_root: Path) -> sqlite3.Connection:
    """Open the original memory store with a single transactional schema writer.

    The installed v2 database historically recorded its version only in meta,
    leaving PRAGMA user_version=0. Accept that known form, never silently
    overwrite future or contradictory schema markers or repair broken tables.
    """
    path = db_path(lane_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ) if row[0] in _TABLE_COLUMNS
            }
            if user_version not in (0, 1, SCHEMA_VERSION):
                raise RuntimeError("DEVELOPMENT_MEMORY_UNSUPPORTED_SCHEMA")
            if tables and not {"attempts", "rules"}.issubset(tables):
                raise RuntimeError("DEVELOPMENT_MEMORY_PARTIAL_SCHEMA")
            marker = None
            if "meta" in tables:
                record = connection.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()
                marker = record[0] if record is not None else None
                if marker not in (None, "1", str(SCHEMA_VERSION)):
                    raise RuntimeError("DEVELOPMENT_MEMORY_UNSUPPORTED_SCHEMA")
            if (user_version and marker is not None and str(user_version) != marker
                    or user_version == SCHEMA_VERSION and marker is None
                    or marker == str(SCHEMA_VERSION) and "strategy_exposures" not in tables
                    or "strategy_exposures" in tables and marker != str(SCHEMA_VERSION)):
                raise RuntimeError("DEVELOPMENT_MEMORY_SCHEMA_VERSION_CONFLICT")
            for name in tables:
                columns = tuple(
                    row["name"] for row in connection.execute(f"PRAGMA table_info({name})")
                )
                if columns != _TABLE_COLUMNS[name]:
                    raise RuntimeError("DEVELOPMENT_MEMORY_SCHEMA_DRIFT")
            if not tables:
                connection.execute(
                    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute("""
                    CREATE TABLE attempts (
                        packet_id TEXT PRIMARY KEY, item_id TEXT,
                        repository TEXT NOT NULL, base_sha TEXT NOT NULL,
                        module_path TEXT, targets TEXT NOT NULL, method TEXT NOT NULL,
                        model TEXT, instructions TEXT, prompt TEXT, reply TEXT,
                        tests TEXT, static TEXT, failure_class TEXT NOT NULL,
                        outcome TEXT NOT NULL, recorded_at_utc TEXT NOT NULL)
                """)
                connection.execute("""
                    CREATE TABLE rules (
                        failure_class TEXT PRIMARY KEY, rule TEXT NOT NULL,
                        evidence TEXT NOT NULL, slices INTEGER NOT NULL,
                        kind TEXT NOT NULL, derived_at_utc TEXT NOT NULL)
                """)
            elif "meta" not in tables:
                connection.execute(
                    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
            if "strategy_exposures" not in tables:
                connection.execute("""
                    CREATE TABLE strategy_exposures (
                        packet_id TEXT PRIMARY KEY REFERENCES attempts(packet_id),
                        strategy TEXT NOT NULL,
                        revision INTEGER NOT NULL CHECK (revision IN (1, 2)),
                        evidence_packet TEXT NOT NULL REFERENCES attempts(packet_id))
                """)
            connection.execute(
                "INSERT INTO meta VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        result = connection.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok" or connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("DEVELOPMENT_MEMORY_INTEGRITY_FAILED")
        return connection
    except BaseException:
        connection.close()
        raise


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


def _validated_strategy(connection: sqlite3.Connection, packet: dict[str, Any],
                        item: dict[str, Any]) -> tuple[str, int, str] | None:
    """Only a canonically recorded earlier failure may be credited as guidance.

    Never trust worker-written instructions or an arbitrary packet hint as a
    new approved policy. A valid hint has to be one the existing Level-1
    worker's catalog would actually use, bound to the same item and repo.
    """
    hint = packet.get("recovery_strategy")
    if not isinstance(hint, dict) or hint.get("schema_version") != 1:
        return None
    if not isinstance(item.get("build"), dict) or not item.get("id"):
        return None
    from .iron_god_failure_strategy import STRATEGIES

    name, revision, source_id = hint.get("strategy"), hint.get("revision"), hint.get("evidence_packet")
    if (not isinstance(name, str) or name not in STRATEGIES
            or type(revision) is not int or not 1 <= revision <= len(STRATEGIES[name])
            or not isinstance(source_id, str) or not source_id or source_id == packet["packet_id"]):
        return None
    source = connection.execute(
        "SELECT item_id,repository,failure_class,outcome FROM attempts WHERE packet_id=?",
        (source_id,),
    ).fetchone()
    if (source is None or source["item_id"] != item["id"]
            or source["repository"] != packet["repository"]
            or source["failure_class"] != name
            or not str(source["outcome"]).startswith("FAIL")):
        return None
    return name, revision, source_id


def record_attempt(lane_root: Path, packet: dict[str, Any], handback: dict[str, Any], outcome: str) -> str:
    """One attempt's observed facts (idempotent per packet); returns its failure class."""
    # a review repair is recorded against the slice whose pull request it repairs
    item = packet.get("slice") or (packet.get("repair") or {}).get("slice") or {}
    plan = handback.get("plan") or {}
    directed = handback.get("directed") or {}
    # Level-1 workers report their final targeted pytest/mypy/ruff output in
    # verification[].output_tail, not directed.test_failure (Level 2 only).
    # Prefer the *final failing* check over first_test_failure: the first check
    # may have been repaired, and must not overwrite the actual last result.
    # This is classification of observed diagnostics, never an acceptance oracle.
    diagnostics = directed.get("test_failure")
    if not diagnostics and (packet.get("slice") or {}).get("build"):
        checks = handback.get("verification") or []
        if isinstance(checks, list):
            failed = [row.get("output_tail") for row in checks
                      if isinstance(row, dict) and row.get("ok") is False
                      and isinstance(row.get("output_tail"), str)]
            if failed:
                diagnostics = "\\n".join(failed)[-3000:]
        if not diagnostics and not checks and isinstance(handback.get("first_test_failure"), str):
            diagnostics = handback["first_test_failure"][-3000:]
    evidence = {**directed, "test_failure": diagnostics} if diagnostics else directed
    failure_class = classify(str(handback.get("status", "")), str(handback.get("summary", "")), evidence)
    instructions = {"direction": plan.get("direction"), "objective": plan.get("objective"),
                    "add": plan.get("add"), "test_path": plan.get("test_path")}
    values = (
        packet["packet_id"], item.get("id"), packet["repository"], packet["base_sha"], plan.get("module_path"),
        json.dumps(plan.get("targets") or []), plan.get("mode") or ("build" if plan else "none"),
        directed.get("model"), json.dumps(instructions), directed.get("prompt"), directed.get("reply"),
        json.dumps(handback.get("verification") or []), diagnostics, failure_class,
        outcome[:300], _now(),
    )
    # A packet is a single historical observation. A repeated handback must
    # neither replace it nor silently rewrite its classification/learning.
    # INSERT OR IGNORE first serializes competing SQLite writers; the ensuing
    # read checks the actual committed winner inside the same transaction.
    with closing(_connect(lane_root)) as connection, connection:
        inserted = connection.execute(
            "INSERT OR IGNORE INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values,
        ).rowcount == 1
        existing = connection.execute(
            "SELECT * FROM attempts WHERE packet_id=?", (packet["packet_id"],),
        ).fetchone()
        if existing is None or tuple(existing)[:-1] != values[:-1]:
            # recorded_at_utc is intentionally excluded: identical replays
            # occur at different times but are not distinct attempts.
            raise sqlite3.IntegrityError("ATTEMPT_REPLAY_CONFLICT")
        exposure = _validated_strategy(connection, packet, item)
        stored = connection.execute(
            "SELECT strategy,revision,evidence_packet FROM strategy_exposures WHERE packet_id=?",
            (packet["packet_id"],),
        ).fetchone()
        if inserted:
            if exposure is not None:
                connection.execute(
                    "INSERT INTO strategy_exposures VALUES (?,?,?,?)",
                    (packet["packet_id"], *exposure),
                )
        elif (tuple(stored) if stored is not None else None) != exposure:
            # Prevent a later duplicate handback from attributing an old
            # unadvised pass to newly supplied guidance.
            raise sqlite3.IntegrityError("ATTEMPT_REPLAY_CONFLICT")
    return failure_class


def derive_rules(lane_root: Path) -> list[dict[str, Any]]:
    """Recommendations require independent *lineages*, not repairs of one slice.

    Repeated -rN attempts cannot inflate the evidence threshold. Historical
    inferred rules that no longer qualify under this normalization are
    removed, while every original attempt remains immutable.
    """
    from . import iron_god_backlog as backlog

    with closing(_connect(lane_root)) as connection, connection:
        rows = connection.execute("SELECT failure_class, item_id, packet_id FROM attempts "
                                  "WHERE item_id IS NOT NULL ORDER BY recorded_at_utc").fetchall()
        groups: dict[str, dict[str, list[str]]] = {}
        for row in rows:
            lineage = backlog.lineage_base(row["item_id"])
            groups.setdefault(row["failure_class"], {}).setdefault(lineage, []).append(row["packet_id"])
        supported = {
            failure_class for failure_class, slices in groups.items()
            if failure_class in RULE_CATALOG and len(slices) >= MIN_INDEPENDENT_SLICES
        }
        # Old databases may contain rules derived by incorrectly counting
        # retries as separate slices. Retire only inferred rows; never erase
        # the underlying attempts or externally governed knowledge.
        for old in connection.execute(
            "SELECT failure_class FROM rules WHERE kind='INFERRED_RECOMMENDATION'"
        ).fetchall():
            if old["failure_class"] not in supported:
                connection.execute(
                    "DELETE FROM rules WHERE failure_class=? AND kind='INFERRED_RECOMMENDATION'",
                    (old["failure_class"],),
                )
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
    """First-attempt acceptance per independent lineage, not each -rN retry."""
    from . import iron_god_backlog as backlog

    with closing(_connect(lane_root)) as connection:
        rows = connection.execute("SELECT item_id, failure_class, outcome FROM attempts WHERE item_id IS NOT NULL "
                                  "ORDER BY recorded_at_utc").fetchall()
    first: dict[str, Any] = {}
    classes: dict[str, int] = {}
    for row in rows:
        first.setdefault(backlog.lineage_base(row["item_id"]), row)
        classes[row["failure_class"]] = classes.get(row["failure_class"], 0) + 1
    judged = [r for r in first.values() if r["failure_class"] not in
              {"COMPLETED_EXTERNALLY", "BLOCKED_CONFLICT", "ENVIRONMENT", "TEST_NOT_DISCRIMINATING"}]
    accepted = sum(1 for r in judged if r["failure_class"] == "PASSED")
    return {"attempts": len(rows), "slices": len(first), "first_attempt_judged": len(judged),
            "first_attempt_accepted": accepted,
            "first_attempt_rate": round(accepted / len(judged), 3) if judged else None, "failure_classes": classes}


def _already_backfilled(lane_root: Path, packet: dict[str, Any]) -> bool:
    """Check canonical packet identity; mutable backlog outcome is not packet history."""
    db = db_path(lane_root)
    if not db.is_file():
        return False
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        existing = connection.execute(
            "SELECT item_id, repository, base_sha FROM attempts WHERE packet_id=?",
            (packet["packet_id"],),
        ).fetchone()
    if existing is None:
        return False
    item = packet.get("slice") or (packet.get("repair") or {}).get("slice") or {}
    if (existing[0] != item.get("id") or existing[1] != packet.get("repository")
            or existing[2] != packet.get("base_sha")):
        raise sqlite3.IntegrityError("ATTEMPT_REPLAY_CONFLICT")
    return True


def backfill(lane_root: Path) -> int:
    """Import missing historical attempts; never rewrite an existing packet.

    Item-level backlog outcome is mutable (latest retry/merge), so it can only
    describe the final packet. Older unrecorded packets retain their worker
    handback status rather than inherit a later packet's accepted outcome.
    """
    lane_root = Path(lane_root)
    try:
        outcomes = json.loads((lane_root / "backlog-state.json").read_text(encoding="utf-8")).get("items", {})
    except (OSError, ValueError):
        outcomes = {}
    by_packet = {row["packets"][-1]: row.get("outcome", "")
                 for row in outcomes.values()
                 if isinstance(row, dict) and isinstance(row.get("packets"), list)
                 and row["packets"]}
    count = 0
    for packet_path in sorted((lane_root / "packets").glob("*.json")):
        try:
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            handback = json.loads((lane_root / "handbacks" / packet_path.name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _already_backfilled(lane_root, packet):
            continue
        try:
            diagnostics = json.loads((lane_root / "reports" / f"{packet_path.stem}-handback-diagnostics.json")
                                     .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            diagnostics = {}
        outcome = str(by_packet.get(packet["packet_id"]) or handback.get("status", ""))
        try:
            record_attempt(lane_root, packet, {**diagnostics, **handback}, outcome)
        except sqlite3.IntegrityError:
            # A live harvester can win the race between read and INSERT. Its
            # canonical observation takes precedence; incompatible identity
            # is still a hard conflict rather than silently accepted.
            if not _already_backfilled(lane_root, packet):
                raise
            continue
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Iron God development memory (read-mostly).")
    parser.add_argument("command", choices=("rules", "stats", "backfill", "feedback"))
    parser.add_argument("--repository")
    parser.add_argument("--lane-root", type=Path, default=Path(os.environ.get("LOCALAPPDATA", "."))
                        / "ExponentialEngine" / "flow-state" / "iron-god-products")
    args = parser.parse_args(argv)
    if args.command == "backfill":
        result: Any = {"recorded": backfill(args.lane_root)}
    elif args.command == "stats":
        result = stats(args.lane_root)
    elif args.command == "feedback":
        from .iron_god_learning_feedback import snapshot
        result = snapshot(args.lane_root, repository=args.repository or "")
    else:
        result = {"rules": rules_for(args.lane_root, args.repository)}
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
