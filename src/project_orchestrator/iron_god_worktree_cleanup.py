"""Evidence-preserving cleanup of finished product-turn worktrees.

Measured on 2026-10-27: every product turn left its worktree behind (56 after one
day, 11.7 GB; knowledge-engine-core tracks ~600 MB of data, so each of its
checkouts costs that much). Committed work never lives only in a worktree: the
turn branch stays in the lane's clone. This tool removes a worktree only after:

1. its turn is finished and not under review (never the active turn, a pull
   request still pending/awaiting CI/awaiting the owner, a REVIEW_REQUIRED turn,
   or anything it cannot classify from the lane's own records);
2. its evidence is archived beside the turn's reports -- HEAD, base, branch and
   branch-ref SHA, ``git status``, the full diff of tracked changes against HEAD
   and the content of untracked files -- and the archive is re-read and its
   SHA-256 verified (an archive over the size cap keeps the worktree);
3. the turn branch ref still points at the worktree's HEAD, so every commit
   remains reachable in the clone after removal.

Dry run by default; ``--apply`` requires the owner to hold control so no turn
can start meanwhile.

    python -m project_orchestrator.iron_god_worktree_cleanup [--apply]

The engine also cleans up after itself (owner, 2026-11-14: "cleanup after yourself and the engine should do the same";
C: had reached 35.03 GiB, the allocation gate's limit, because this tool only ran when someone ran it by hand).
``auto_clean`` applies the same rules from inside the product lane, which calls it only while no turn is active - the
lane is the one thing that starts turns, so none can start meanwhile - at most every ``AUTO_INTERVAL_S`` and at most
``AUTO_MAX_REMOVALS`` worktrees per run. It also takes the worktree an in-engine capable build left behind once that
build is no longer running (an owner takeover kills the runner before it can remove its own).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .state_io import atomic_json

KEEP_PUBLICATION_STATES = {"PUBLISH_PENDING", "AWAITING_CI", "AWAITING_OWNER_REVIEW", "REPAIR_PENDING",
                           "REPAIRING"}
MAX_ARCHIVE_BYTES = 2_000_000
REMOVABLE = {"FAILED_NO_COMMITS", "UNCOMMITTED_CHANGES", "UNPUBLISHED_COMMITS", "MERGED", "PUBLICATION_BLOCKED",
             "CAPABLE_BUILD_FINISHED"}
AUTO_INTERVAL_S = 30 * 60
AUTO_MAX_REMOVALS = 10       # a knowledge-engine-core checkout is ~600 MB: bound the time one lane pass can spend
AUTO_STAMP = "worktree-cleanup-auto.json"
CAPABLE_PREFIX = "capable-"  # iron_god_capable_engine: <worktrees>/capable-<dispatch id>


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False,
                          encoding="utf-8", errors="replace")


def _json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def classify(lane_root: Path, worktree: Path) -> dict[str, Any]:
    """What a worktree holds, from the lane's own records and git; never guesses."""
    pid = worktree.name
    if pid.startswith(CAPABLE_PREFIX):
        return _classify_capable(lane_root, worktree)
    packet = _json(lane_root / "packets" / f"{pid}.json", {})
    handback = _json(lane_root / "handbacks" / f"{pid}.json", {})
    publication = _json(lane_root / "publications.json", {"items": {}}).get("items", {}).get(pid, {})
    active = (_json(lane_root / "active.json", None) or {}).get("packet_id")
    row: dict[str, Any] = {"packet_id": pid, "path": str(worktree), "branch": packet.get("branch"),
                           "base_sha": packet.get("base_sha"), "publication": publication.get("state")}
    head = _git(worktree, "rev-parse", "HEAD")
    row["head_sha"] = head.stdout.strip() if head.returncode == 0 else None
    status = _git(worktree, "status", "--porcelain")
    row["dirty"] = bool(status.stdout.strip()) if status.returncode == 0 else None
    ahead = _git(worktree, "rev-list", "--count", f"{packet.get('base_sha')}..HEAD") if packet.get("base_sha") else None
    row["commits_ahead"] = int(ahead.stdout.strip()) if ahead is not None and ahead.returncode == 0 else None
    if pid == active:
        kind = "ACTIVE"
    elif not packet or row["head_sha"] is None or row["dirty"] is None or row["commits_ahead"] is None:
        kind = "UNKNOWN"
    elif publication.get("state") in KEEP_PUBLICATION_STATES:
        kind = "PUBLICATION_OPEN"
    elif handback.get("status") == "REVIEW_REQUIRED":
        kind = "REVIEW_REQUIRED"
    elif publication.get("state") == "MERGED":
        kind = "MERGED"
    elif publication.get("state") == "BLOCKED":
        kind = "PUBLICATION_BLOCKED"
    elif row["commits_ahead"] > 0:
        kind = "UNPUBLISHED_COMMITS"
    elif row["dirty"]:
        kind = "UNCOMMITTED_CHANGES"
    elif handback:
        kind = "FAILED_NO_COMMITS"
    else:
        kind = "UNKNOWN"
    row["class"] = kind
    return row


def _classify_capable(lane_root: Path, worktree: Path) -> dict[str, Any]:
    """The worktree of an in-engine capable build, from that build's own job file and dispatch row.

    Kept while the dispatch is still DISPATCHED (its runner may be working), when either record is unreadable, and -
    whatever the row says - while that dispatch's runner process still exists or cannot be looked up: the row is a
    record, the process is the fact, and an unknown is never "gone".
    """
    from . import iron_god_capable_builder as capable_builder
    ident = worktree.name[len(CAPABLE_PREFIX):]
    job = _json(lane_root / "capable-engine-jobs" / f"{ident}.job.json", {})
    dispatch = _json(lane_root / "capable-dispatches" / f"{ident}.json", {})
    row: dict[str, Any] = {"packet_id": worktree.name, "path": str(worktree), "branch": job.get("branch"),
                           "base_sha": job.get("base"), "publication": dispatch.get("status")}
    head = _git(worktree, "rev-parse", "HEAD")
    row["head_sha"] = head.stdout.strip() if head.returncode == 0 else None
    status = _git(worktree, "status", "--porcelain")
    row["dirty"] = bool(status.stdout.strip()) if status.returncode == 0 else None
    ahead = _git(worktree, "rev-list", "--count", f"{job.get('base')}..HEAD") if job.get("base") else None
    row["commits_ahead"] = int(ahead.stdout.strip()) if ahead is not None and ahead.returncode == 0 else None
    if not job or not dispatch.get("status") or row["head_sha"] is None or row["dirty"] is None \
            or row["commits_ahead"] is None:
        row["class"] = "UNKNOWN"
    elif dispatch["status"] == "DISPATCHED" or capable_builder.runner_alive(dispatch):
        row["class"] = "ACTIVE"
    else:
        row["class"] = "CAPABLE_BUILD_FINISHED"
    return row


def _evidence(worktree: Path, row: dict[str, Any], clone: Path) -> dict[str, Any]:
    # Lossless: UTF-8 text verbatim, anything else as base64 bytes. A file that
    # cannot be read is listed in "unreadable" and remove_one keeps the worktree.
    untracked: dict[str, Any] = {}
    unreadable: list[str] = []
    listed = _git(worktree, "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
    for name in filter(None, listed):
        try:
            data = (worktree / name).read_bytes()
        except OSError:
            unreadable.append(name)
            continue
        try:
            untracked[name] = data.decode("utf-8")
        except UnicodeDecodeError:
            untracked[name] = {"base64": base64.b64encode(data).decode("ascii"), "bytes": len(data)}
    branch_ref = _git(clone, "rev-parse", "--verify", "-q", f"refs/heads/{row['branch']}").stdout.strip()
    return {**row, "branch_ref_sha": branch_ref or None,
            "status": _git(worktree, "status", "--porcelain").stdout,
            # --binary: a plain diff records only "Binary files differ" for binary changes.
            "diff_against_head": _git(worktree, "diff", "--binary", "HEAD").stdout,
            "unreadable": unreadable,
            "commits": _git(worktree, "log", "--format=%H %s", f"{row['base_sha']}..HEAD").stdout,
            "untracked": untracked}


def plan(state_root: Path) -> list[dict[str, Any]]:
    lane_root = Path(state_root) / "iron-god-products"
    worktrees = Path(state_root).parent / "canon-workspaces" / "products" / "worktrees"
    return [classify(lane_root, path) for path in sorted(worktrees.iterdir()) if path.is_dir()] \
        if worktrees.is_dir() else []


def remove_one(state_root: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Archive, verify, then remove one worktree; any doubt keeps it."""
    worktree = Path(row["path"])
    common = _git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    if not common:
        return {**row, "result": "KEPT_NOT_A_GIT_WORKTREE"}
    clone = Path(common).parent
    evidence = _evidence(worktree, row, clone)
    if evidence["unreadable"]:
        return {**row, "result": "KEPT_UNREADABLE_UNTRACKED_FILE", "unreadable": evidence["unreadable"]}
    if row["commits_ahead"] and evidence["branch_ref_sha"] != row["head_sha"]:
        return {**row, "result": "KEPT_BRANCH_REF_DOES_NOT_HOLD_THE_COMMITS"}
    payload = json.dumps(evidence, indent=2, sort_keys=True)
    if len(payload.encode("utf-8")) > MAX_ARCHIVE_BYTES:
        return {**row, "result": "KEPT_EVIDENCE_TOO_LARGE_TO_ARCHIVE"}
    archive = Path(state_root) / "iron-god-products" / "reports" / "worktree-archive" / f"{row['packet_id']}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(payload.encode("utf-8"))  # exact bytes: text mode would rewrite newlines on Windows
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        return {**row, "result": "KEPT_ARCHIVE_VERIFICATION_FAILED"}
    removed = _git(clone, "worktree", "remove", "--force", str(worktree))
    if removed.returncode != 0 or worktree.exists():
        return {**row, "result": "KEPT_REMOVE_FAILED", "detail": (removed.stderr or "")[-300:]}
    if row["branch"] and row["commits_ahead"]:
        still = _git(clone, "rev-parse", "--verify", "-q", f"refs/heads/{row['branch']}").stdout.strip()
        if still != row["head_sha"]:
            return {**row, "result": "REMOVED_BUT_BRANCH_CHANGED", "archive": str(archive)}
    return {**row, "result": "REMOVED", "archive": str(archive), "archive_sha256": digest}


def auto_clean(state_root: Path, *, now: float | None = None, interval: float = AUTO_INTERVAL_S,
               limit: int = AUTO_MAX_REMOVALS) -> dict[str, Any] | None:
    """The engine's own cleanup: the same rules as ``--apply``, a bounded number of worktrees, at most every ``interval``.

    The CALLER guarantees no turn can start meanwhile (the product lane calls this only while it has no active turn).
    Returns None when it is not due, else what it did; the report is also the stamp for the next run.
    """
    now = time.time() if now is None else now
    stamp = Path(state_root) / "iron-god-products" / "reports" / AUTO_STAMP
    last = _json(stamp, {}).get("ran_at")
    if isinstance(last, (int, float)) and 0 <= now - last < interval:  # never run, or an unreadable stamp: it is due
        return None
    rows = plan(state_root)
    eligible = [row for row in rows if row["class"] in REMOVABLE]
    # Worktrees the last run could not remove (evidence too large, a locked file) go to the back of the queue, so they
    # can never keep the ones behind them from being tried; they are still retried once everything else is done.
    stuck = set(_json(stamp, {}).get("not_removed") or [])
    queue = sorted(eligible, key=lambda row: row["packet_id"] in stuck)
    results: list[dict[str, Any]] = []
    for row in queue[:2 * limit]:  # attempts are bounded too: a failed attempt still costs an archive
        if sum(1 for r in results if r["result"] == "REMOVED") >= limit:
            break
        results.append(remove_one(state_root, row))
    removed = sum(1 for r in results if r["result"] == "REMOVED")
    report = {"ran_at": now, "worktrees": len(rows), "eligible": len(eligible), "removed": removed,
              "left_for_next_run": len(eligible) - removed,
              "not_removed": sorted((stuck & {row["packet_id"] for row in eligible}
                                     - {r["packet_id"] for r in results})
                                    | {r["packet_id"] for r in results if r["result"] != "REMOVED"}),
              "results": {r["packet_id"]: r["result"] for r in results},
              "kept": {r["packet_id"]: r["class"] for r in rows if r["class"] not in REMOVABLE}}
    atomic_json(stamp, report)
    return report


def _size_mb(path: Path) -> float:
    return round(sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evidence-preserving product worktree cleanup (dry run by default)")
    parser.add_argument("--state-root", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", "")) / "ExponentialEngine" / "flow-state")
    parser.add_argument("--apply", action="store_true", help="archive and remove eligible worktrees")
    args = parser.parse_args(argv)
    rows = plan(args.state_root)
    for row in rows:
        row["size_mb"] = _size_mb(Path(row["path"]))
    eligible = [r for r in rows if r["class"] in REMOVABLE]
    summary: dict[str, Any] = {
        "worktrees": len(rows), "eligible": len(eligible),
        "eligible_mb": round(sum(r["size_mb"] for r in eligible), 1),
        "kept": {r["packet_id"]: r["class"] for r in rows if r["class"] not in REMOVABLE},
        "by_class": {k: sum(1 for r in rows if r["class"] == k) for k in sorted({r["class"] for r in rows})},
    }
    if args.apply:
        from . import iron_god_owner_control as owner_controller

        if owner_controller.owner_status(owner_controller.control_root(args.state_root)) != \
                owner_controller.OWNER_CONTROL_ACTIVE:
            print(json.dumps({"status": "REFUSED", "reason": "OWNER_MUST_HOLD_CONTROL"}))
            return 3
        results = [remove_one(args.state_root, r) for r in eligible]
        summary["results"] = {r["packet_id"]: r["result"] for r in results}
        summary["removed"] = sum(1 for r in results if r["result"] == "REMOVED")
        report = args.state_root / "iron-god-products" / "reports" / "worktree-cleanup-last.json"
        atomic_json(report, {"summary": summary, "results": results})
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
