"""Read-only verification of whether Iron God's recorded recovery guidance helped.

This is a projection of the existing SQLite attempt ledger, not an authority,
model-training store, scheduler, or substitute for independently verified PRs.
Worker PASS and ACCEPTED_LOCAL are *local observations*, never product proof.
The independent lineage count is deliberately conservative: retries and
re-authorings of the same roadmap task count at most once per strategy.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

from . import iron_god_backlog as backlog
from . import iron_god_development_memory as memory
from .iron_god_failure_strategy import STRATEGIES

MAX_ROWS = 5000
MIN_INDEPENDENT_LINEAGES = 3
MIN_LOCAL_PASSES_FOR_REVIEW = 2


def _empty(status: str, *, observed: int = 0) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": memory.DB_NAME,
        "status": status,
        "worker_attempts_observed": observed,
        "strategies_observed": 0,
        "independent_recovery_lineages": 0,
        "baseline_first_attempt_lineages": 0,
        "baseline_first_attempt_local_passes": 0,
        "strategies": [],
        "learning_effectiveness_proven": False,
        "independent_github_merge_verified": False,
        "native_product_acceptance": "UNVERIFIED",
        "automatic_promotion_authorized": False,
        "new_worker_or_owner_grant": False,
        "comparison_type": "OBSERVATIONAL_NOT_CAUSAL",
    }


def _local_outcome(row: sqlite3.Row) -> str:
    """Local acceptance is intentionally stricter than a worker's PASS word."""
    if row["failure_class"] == "PASSED" and row["outcome"] == "ACCEPTED_LOCAL":
        return "WORKER_ACCEPTED_LOCALLY"
    if str(row["outcome"]).startswith("FAIL") and row["failure_class"] != "ENVIRONMENT":
        return "WORKER_FAILED"
    return "UNJUDGED"


def snapshot(lane_root: Path, *, repository: str = "") -> dict[str, Any]:
    """Bounded, non-mutating snapshot; fail closed on missing/corrupt/large data."""
    if repository:
        from .iron_god_observability import REPOSITORIES

        if repository not in REPOSITORIES:
            return _empty("UNKNOWN_REPOSITORY")
    database = memory.db_path(lane_root)
    if not database.is_file() or database.is_symlink():
        return _empty("UNAVAILABLE")
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_exposures'"
            ).fetchone()
            where = "WHERE a.repository=?" if repository else ""
            values: tuple[Any, ...] = (repository, MAX_ROWS + 1) if repository else (MAX_ROWS + 1,)
            count = db.execute(
                "SELECT COUNT(*) FROM attempts" + (" WHERE repository=?" if repository else ""),
                (repository,) if repository else (),
            ).fetchone()[0]
            if not table:
                return _empty("LEGACY_LEDGER_NO_EXPOSURES", observed=count)
            rows = db.execute(
                "SELECT a.rowid AS attempt_order,a.packet_id,a.item_id,a.repository,"
                "a.failure_class,a.outcome,a.recorded_at_utc,s.strategy,s.revision,s.evidence_packet "
                "FROM attempts a LEFT JOIN strategy_exposures s ON s.packet_id=a.packet_id "
                + where + " ORDER BY a.rowid LIMIT ?",
                values,
            ).fetchall()
        if len(rows) > MAX_ROWS:
            return _empty("TRUNCATED_HISTORY_NO_EFFECTIVENESS_CLAIM", observed=count)
        if not rows:
            return _empty("NO_ATTEMPTS")
        by_packet = {r["packet_id"]: r for r in rows}
        first_attempt: dict[tuple[str, str], sqlite3.Row] = {}
        latest_exposure: dict[tuple[str, str], sqlite3.Row] = {}
        for row in rows:
            if not isinstance(row["item_id"], str) or not row["item_id"]:
                continue
            lineage = (row["repository"], backlog.lineage_base(row["item_id"]))
            first_attempt.setdefault(lineage, row)
            name = row["strategy"]
            if name not in STRATEGIES or type(row["revision"]) is not int:
                continue
            if not 1 <= row["revision"] <= len(STRATEGIES[name]):
                continue
            source = by_packet.get(row["evidence_packet"])
            if (source is None or source["packet_id"] == row["packet_id"]
                    or source["item_id"] != row["item_id"]
                    or source["repository"] != row["repository"]
                    or source["failure_class"] != name
                    or not str(source["outcome"]).startswith("FAIL")
                    # Windows may assign identical UTC timestamps to adjacent
                    # immutable SQLite attempts. The actual append order, not
                    # the coarse system clock, establishes prior evidence.
                    or source["attempt_order"] >= row["attempt_order"]):
                continue
            # The latest observed strategy wins within this lineage; retries
            # must never appear as multiple independent learning samples.
            latest_exposure[lineage] = row

        cohorts: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
        for row in latest_exposure.values():
            cohorts[(row["strategy"], row["revision"])].append(row)
        strategies = []
        for (name, revision), cohort in sorted(cohorts.items()):
            passed = sum(_local_outcome(row) == "WORKER_ACCEPTED_LOCALLY" for row in cohort)
            failed = sum(_local_outcome(row) == "WORKER_FAILED" for row in cohort)
            sufficient = len(cohort) >= MIN_INDEPENDENT_LINEAGES and passed >= MIN_LOCAL_PASSES_FOR_REVIEW
            strategies.append({
                "strategy": name,
                "revision": revision,
                "independent_lineages": len(cohort),
                "worker_reported_local_passes": passed,
                "worker_reported_failures": failed,
                "unjudged": len(cohort) - passed - failed,
                "status": "CANDIDATE_FOR_INDEPENDENT_REVIEW" if sufficient
                          else "INSUFFICIENT_INDEPENDENT_LOCAL_EVIDENCE",
                "can_change_owner_policy": False,
                "requires_exact_head_ci_and_native_product_proof": True,
            })
        baseline = list(first_attempt.values())
        report = _empty("OBSERVED_WORKER_FEEDBACK", observed=count)
        report.update({
            "strategies_observed": sum(1 for row in rows if row["strategy"] in STRATEGIES),
            "independent_recovery_lineages": len(latest_exposure),
            "baseline_first_attempt_lineages": len(baseline),
            "baseline_first_attempt_local_passes": sum(
                _local_outcome(row) == "WORKER_ACCEPTED_LOCALLY" for row in baseline
            ),
            "strategies": strategies,
        })
        return report
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        return _empty("UNVERIFIED")
