"""Evidence-based *advisory* strategy changes for repeated local Python builds.

The accepted spec, test oracle, scope, model calibration, attempts and CI gates
are untouched. Only a pre-reviewed coding hint changes after a genuinely failed
worker packet whose classification matches the durable development-memory row.
This reuses the existing SQLite experience store and backlog attempt ledger;
it is neither a new planner nor permission to spend cloud tokens.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from . import iron_god_backlog as backlog
from . import iron_god_development_memory as memory

# Reviewed, bounded recovery approaches. No worker-authored text is executed or
# promoted to an instruction. The second attempt MUST use a different strategy.
STRATEGIES = {
    "SLICE_TESTS_FAILED": (
        "Re-derive the required behavior directly from every approved example and its boundary cases. "
        "Implement a fresh algorithm instead of reproducing the previous unsuccessful implementation.",
        "Switch from the previous algorithm to an explicit input-case decision table, including edge and "
        "invalid-input cases required by the approved examples. Implement from that table, not the last solution.",
    ),
    "STATIC_MYPY_UNTYPED": (
        "Use explicit type annotations on all introduced functions and nested helpers, including return types. "
        "Prefer simple typed values that the repository's strict checker can verify.",
        "Replace complex or nested helper structure with straightforward, fully typed expressions. "
        "Avoid the previous implementation structure that triggered the type-check errors.",
    ),
    "STATIC_LINE_LENGTH": (
        "Change the formatting approach: split complex expressions into named intermediate values and follow "
        "the repository's actual lint and formatting configuration.",
        "Change the implementation structure to avoid long expressions entirely, using short helper values "
        "and multiline constructs permitted by the repository's formatter.",
    ),
}


def recovery_strategy(lane_root: Path, item: dict[str, Any], row: dict[str, Any]) -> dict[str, Any] | None:
    """Select one verified, changed approach after an actual failed build.

    Unknown/corrupt/stale evidence means no advice; it never weakens dispatch.
    Only Level-1 owner-approved, already-attempted work is in this slice. A
    Level-2 directed edit has its own independent approval/failing-test regime.
    """
    if (not isinstance(item, dict) or not isinstance(row, dict)
            or item.get("status") != "approved"
            or item.get("assignee") != backlog.AGENT_ID
            or item.get("level") != 1 or not isinstance(item.get("build"), dict)):
        return None
    packet_ids = row.get("packets")
    attempts = row.get("attempts")
    if (not isinstance(packet_ids, list) or not packet_ids
            or not isinstance(packet_ids[-1], str) or not packet_ids[-1]
            or type(attempts) is not int or not 1 <= attempts <= 2
            or backlog.repeated_failed_source(row)):
        return None
    outcome = str(row.get("outcome") or "")
    if not outcome.startswith("FAIL"):
        return None
    db = memory.db_path(Path(lane_root))
    if not db.is_file():
        return None
    try:
        # Existing database is read-only: consultation must not create memory
        # or mutate an existing record to make a retry look better.
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            fact = connection.execute(
                "SELECT item_id, repository, failure_class, outcome FROM attempts WHERE packet_id = ?",
                (packet_ids[-1],),
            ).fetchone()
    except (sqlite3.Error, OSError, ValueError):
        return None
    if (fact is None or fact[0] != item.get("id") or fact[1] != item.get("repository")
            or fact[2] not in STRATEGIES or not str(fact[3]).startswith("FAIL")):
        return None
    return {
        "schema_version": 1, "strategy": fact[2], "revision": attempts,
        "evidence_packet": packet_ids[-1],
    }


def coding_guidance(packet: dict[str, Any]) -> str:
    """Return catalogued guidance, never arbitrary packet/worker-provided text."""
    if not isinstance((packet.get("slice") or {}).get("build"), dict):
        return ""
    hint = packet.get("recovery_strategy")
    if not isinstance(hint, dict) or hint.get("schema_version") != 1:
        return ""
    name, attempt = hint.get("strategy"), hint.get("revision")
    if not isinstance(name, str) or name not in STRATEGIES or type(attempt) is not int or attempt not in (1, 2):
        return ""
    return (
        "RECOVERY STRATEGY (advisory, from a verified previous failure): "
        + STRATEGIES[name][attempt - 1]
        + " Keep the owner-approved function signature, acceptance examples, test files, "
          "allowed scope and all verification gates unchanged."
    )
