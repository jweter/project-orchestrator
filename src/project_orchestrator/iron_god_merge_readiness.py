"""Merge readiness for Iron God's automatic product merges (owner-approved capability, 2026-10-30).

A green CI rollup is not enough to merge. Immediately before every automatic merge the product lane
asks this gate, which composes the portfolio's established review requirements from
``scripts/green_pr_merger.py`` (``decide`` and ``blocking_threads``) instead of re-deriving a weaker
copy, and adds the lane's own facts:

1. the pull request's current head is exactly the head Iron God verified;
2. the verification that accepted that head (supervisor re-check + canonical preflight) passed;
3. the pull request targets ``main`` (no unmerged base dependency);
4. code review has settled on the current head (green_pr_merger's quiet window);
5. no review conversation is unresolved -- a finding on the current head is a repair, a finding on
   an older head that Iron God already repaired may be resolved, any other is a repair too;
6. green_pr_merger.decide: mergeable state CLEAN (branch protection, conflicts, required checks),
   review decision, allowed author, and every check completed and passing.

The decision is pure; the lane gathers the inputs and performs the action.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

MERGE = "MERGE"
WAIT = "WAIT"
REPAIR = "REPAIR"
RESOLVE = "RESOLVE"
BLOCKED = "BLOCKED"

# green_pr_merger.decide reasons that clear up on their own (checks still running, GitHub still
# computing mergeability, a young PR); anything else is a real blocker.
_WAIT_REASONS = {"PR_AGE_WINDOW", "REVIEW_SETTLE_WINDOW", "CHECKS_PENDING", "MERGE_STATE_UNKNOWN",
                 "MERGE_STATE_BEHIND", "MERGE_STATE_BLOCKED"}


@dataclass(frozen=True)
class Readiness:
    status: str
    reason: str
    findings: list[dict[str, Any]] = field(default_factory=list)


def load_merger(release_repo: Path) -> ModuleType:
    """The exact green_pr_merger shipped with the running release (repo checkout as a fallback)."""
    path = Path(release_repo) / "scripts" / "green_pr_merger.py"
    if not path.is_file():
        path = Path(__file__).resolve().parents[2] / "scripts" / "green_pr_merger.py"
    spec = importlib.util.spec_from_file_location("iron_god_green_pr_merger", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("GREEN_PR_MERGER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules while loading
    spec.loader.exec_module(module)
    return module


FINDING_COMMENTS = 6
FINDING_COMMENT_CHARS = 1500


def finding(thread: dict[str, Any], head_sha: str, merger: ModuleType) -> dict[str, Any]:
    """One review conversation as repair input: every comment with its commit, not just the first one.

    A reviewer's follow-up on the current head ("still wrong because ...") is exactly what the next
    repair must address, so the original finding and the latest follow-ups are all kept, in order.
    """
    comments = thread.get("comments") or []
    kept = comments if len(comments) <= FINDING_COMMENTS else [comments[0], *comments[-(FINDING_COMMENTS - 1):]]
    rows = [{"commit": str(c.get("commit") or ""), "on_current_head": c.get("commit") == head_sha,
             "body": str(c.get("body", ""))[:FINDING_COMMENT_CHARS]} for c in kept]
    return {"thread_id": thread.get("id"), "path": thread.get("path"), "line": thread.get("line"),
            "commit": next((c.get("commit") for c in comments if c.get("commit")), ""),
            "on_current_head": any(c.get("commit") == head_sha for c in comments),
            "blocking": bool(merger.blocking_threads([thread], head_sha)),
            "comments": rows,
            "body": "\n".join(("" if i == 0 else f"Follow-up on {r['commit'][:7]}: ") + r["body"]
                              for i, r in enumerate(rows))}


def assess(pr: dict[str, Any], threads: list[dict[str, Any]], *, verified_head: str, verification_ok: bool,
           settled: bool, age_seconds: float, repaired_thread_ids: set[str], merger: ModuleType) -> Readiness:
    """Decide MERGE / WAIT / REPAIR / RESOLVE / BLOCKED for one pull request at one instant."""
    head = str(pr.get("headRefOid") or "")
    if not verification_ok:
        return Readiness(BLOCKED, "VERIFICATION_EVIDENCE_MISSING")
    if head != verified_head:
        return Readiness(BLOCKED, "STALE_VERIFIED_HEAD")  # a commit Iron God did not verify
    if pr.get("baseRefName") != "main":
        return Readiness(BLOCKED, "BASE_NOT_MAIN")
    if str(pr.get("state") or "OPEN") != "OPEN":
        return Readiness(BLOCKED, f"PULL_REQUEST_{pr.get('state')}")
    if not settled:
        return Readiness(WAIT, "REVIEW_SETTLE_WINDOW")
    unresolved = [t for t in threads if not t.get("resolved")]
    current = [t for t in unresolved if any(c.get("commit") == head for c in t.get("comments") or [])]
    if current:
        return Readiness(REPAIR, "REVIEW_FINDINGS_ON_CURRENT_HEAD",
                         [finding(t, head, merger) for t in current])
    unrepaired = [t for t in unresolved if t.get("id") not in repaired_thread_ids]
    if unrepaired:
        return Readiness(REPAIR, "UNRESOLVED_REVIEW_FINDINGS", [finding(t, head, merger) for t in unrepaired])
    if unresolved:  # every remaining thread was repaired by a verified, settled, finding-free head
        return Readiness(RESOLVE, "REPAIRED_FINDINGS_READY_TO_RESOLVE", [finding(t, head, merger) for t in unresolved])
    decision = merger.decide(pr, blocking_findings=False, age_seconds=age_seconds, review_settled=True)
    if decision.eligible:
        return Readiness(MERGE, decision.reason)
    if decision.reason in _WAIT_REASONS:
        return Readiness(WAIT, decision.reason)
    if decision.reason == "MERGE_STATE_DIRTY":
        return Readiness(BLOCKED, "MERGE_CONFLICT")
    return Readiness(BLOCKED, decision.reason)
