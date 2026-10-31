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
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .state_io import atomic_json

KEEP_PUBLICATION_STATES = {"PUBLISH_PENDING", "AWAITING_CI", "AWAITING_OWNER_REVIEW"}
MAX_ARCHIVE_BYTES = 2_000_000
REMOVABLE = {"FAILED_NO_COMMITS", "UNCOMMITTED_CHANGES", "UNPUBLISHED_COMMITS", "MERGED", "PUBLICATION_BLOCKED"}


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
