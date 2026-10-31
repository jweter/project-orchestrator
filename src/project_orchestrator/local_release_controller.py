"""Pinned, locally verified release staging for the Iron God runtime.

This module NEVER changes the live actor checkout, supervisor, Windows tasks,
owner-control marker, or candidate-execution authorization. It creates an
independent immutable-by-policy Git checkout and receipt for controlled cutover.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SCHEMA = 1


class ReleaseError(RuntimeError):
    """Refuse a release when identity, provenance or verification is uncertain."""


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=60,
    )
    if proc.returncode != 0:
        raise ReleaseError(f"Git {args[0]} failed: {proc.stderr.strip()[:350]}")
    return proc.stdout.strip()


def _valid_sha(sha: str) -> str:
    if not isinstance(sha, str) or _SHA.fullmatch(sha) is None:
        raise ReleaseError("release requires an exact lowercase 40-character Git SHA")
    return sha


def owner_allows_preparation(owner_state_root: Path, *, owner_directed: bool = False) -> None:
    """Owner authority is checked even for preparing a new release.

    Authority comes only from the authoritative owner controller. An
    autonomous caller needs a live acknowledged Take the Wheel grant. An
    owner-directed preparation (the owner, or an operator the owner has
    instructed, running from the active console session) is permitted
    without a grant: staging only writes a frozen release and its receipt,
    never activates a runtime, and activation still requires Take the Wheel.
    """
    from .human_ownership import AUTHORIZED, owner_control_status
    from .iron_god_owner_control import OwnerControlError, verify_active_console_session

    if owner_directed:
        try:
            verify_active_console_session()
        except OwnerControlError as exc:
            raise ReleaseError(
                f"owner control: owner-directed preparation requires the active console session ({exc.code})"
            ) from exc
        return
    if owner_control_status(owner_state_root) != AUTHORIZED:
        raise ReleaseError("owner control does not authorize release preparation")


def verify_clean_checkout(repo: Path, sha: str) -> dict[str, str]:
    """Reject a dirty source, stale head, non-Git path, or symlinked checkout."""
    _valid_sha(sha)
    if not repo.is_dir() or repo.is_symlink():
        raise ReleaseError("release source is not a regular directory")
    path = repo.resolve(strict=True)
    git_root = Path(_git(path, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if git_root != path:
        raise ReleaseError("release source must be the exact Git checkout root")
    actual = _git(path, "rev-parse", "HEAD")
    if actual != sha:
        raise ReleaseError("release candidate no longer matches exact HEAD")
    if _git(path, "status", "--porcelain", "--untracked-files=normal"):
        raise ReleaseError("release candidate has uncommitted or untracked changes")
    return {"repo": str(path), "sha": sha, "tree": _git(path, "rev-parse", "HEAD^{tree}")}


def _verify_native_preflight(repo: Path, sha: str) -> dict[str, str]:
    """A passing exact-head repository-native full preflight, not model prose."""
    command = [sys.executable, "engineering/preflight.py"]
    if not (repo / "engineering" / "preflight.py").is_file():
        raise ReleaseError("native preflight entrypoint is missing")
    before = _git(repo, "rev-parse", "HEAD")
    if before != sha:
        raise ReleaseError("candidate changed before preflight")
    try:
        proc = subprocess.run(
            command, cwd=repo, capture_output=True, text=True,
            # The complete bounded suite includes 300s controller proofs and
            # 900s regression tests, plus validation/clone overhead. Keep an
            # independent finite outer deadline; never silently skip gates.
            encoding="utf-8", errors="replace", check=False, timeout=1500,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReleaseError("native preflight timed out") from exc
    if proc.returncode != 0:
        excerpt = (proc.stderr + "\n" + proc.stdout)[-1400:]
        raise ReleaseError("native preflight FAILED: " + excerpt)
    after = _git(repo, "rev-parse", "HEAD")
    if after != sha:
        raise ReleaseError("candidate changed during preflight")
    evidence = (proc.stdout + "\n" + proc.stderr).encode("utf-8")
    return {
        "command": " ".join(command),
        "exact_head": sha,
        "exit_code": "0",
        "output_sha256": hashlib.sha256(evidence).hexdigest(),
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _clone_exact_commit(source: Path, destination: Path, sha: str, tree: str) -> Path:
    """Create a clean detached checkout of one verified commit."""
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-local",
            "--no-checkout",
            str(source),
            str(destination),
        ],
        capture_output=True,
        check=True,
        timeout=300,
    )
    _git(destination, "checkout", "--detach", sha)
    if verify_clean_checkout(destination, sha)["tree"] != tree:
        raise ReleaseError("cloned Git tree does not match verified source")
    return destination


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".release-receipt-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(payload, out, indent=2, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_staged_release(receipt: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(receipt, dict) or receipt.get("schema_version") != _SCHEMA:
        raise ReleaseError("release receipt schema mismatch")
    if receipt.get("status") != "VERIFIED_STAGED":
        raise ReleaseError("release has not passed staging")
    sha = _valid_sha(receipt.get("accepted_sha"))
    release_path = receipt.get("release_repo")
    if not isinstance(release_path, str) or not Path(release_path).is_absolute():
        raise ReleaseError("release receipt has no absolute checkout path")
    release = Path(release_path)
    identified = verify_clean_checkout(release, sha)
    if identified["tree"] != receipt.get("tree"):
        raise ReleaseError("release tree differs from verified candidate")
    verification = receipt.get("verification")
    if not isinstance(verification, dict) or verification.get("exact_head") != sha:
        raise ReleaseError("release preflight receipt does not match exact head")
    if verification.get("exit_code") != "0":
        raise ReleaseError("release preflight did not pass")
    return receipt


def stage_local_release(
    candidate: Path, sha: str, releases_root: Path, receipt_root: Path, owner_state_root: Path,
    *, owner_directed: bool = False,
) -> dict[str, Any]:
    """Test + stage a frozen release; deliberately does not activate the supervisor."""
    owner_allows_preparation(owner_state_root, owner_directed=owner_directed)
    source = verify_clean_checkout(candidate, sha)
    releases_root = releases_root.resolve()
    receipts = receipt_root.resolve()
    if releases_root == candidate.resolve() or candidate.resolve() in releases_root.parents:
        raise ReleaseError("release root overlaps its candidate source")
    target = releases_root / sha
    receipt_path = receipts / (sha + ".json")
    if target.exists() or receipt_path.exists():
        if not target.is_dir() or not receipt_path.is_file():
            raise ReleaseError("partial release exists; manual reconciliation required")
        payload = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        if (payload.get("accepted_sha") != sha
                or Path(payload.get("release_repo", "")).resolve() != target.resolve()
                or Path(payload.get("source_repo", "")).resolve() != candidate.resolve()):
            raise ReleaseError("existing release receipt conflicts with requested source")
        validate_staged_release(payload)
        return {**payload, "status": "ALREADY_VERIFIED_STAGED"}
    verification_tempdir = Path(tempfile.mkdtemp(prefix="iron-god-release-verify-"))
    try:
        verification_checkout = _clone_exact_commit(
            candidate,
            verification_tempdir / "checkout",
            sha,
            source["tree"],
        )
        verification = _verify_native_preflight(verification_checkout, sha)
    finally:
        shutil.rmtree(verification_tempdir, ignore_errors=True)
    if verify_clean_checkout(candidate, sha)["tree"] != source["tree"]:
        raise ReleaseError("candidate tree changed during verification")
    owner_allows_preparation(owner_state_root, owner_directed=owner_directed)
    releases_root.mkdir(parents=True, exist_ok=True)
    staging_tempdir = Path(tempfile.mkdtemp(prefix=".local-release-", dir=releases_root))
    try:
        staged = _clone_exact_commit(
            candidate,
            staging_tempdir / "checkout",
            sha,
            source["tree"],
        )
        owner_allows_preparation(owner_state_root, owner_directed=owner_directed)
        if target.exists():
            raise ReleaseError("release target appeared during staging")
        os.replace(staged, target)
    finally:
        # Only delete the uniquely created staging directory, never a pre-existing release.
        shutil.rmtree(staging_tempdir, ignore_errors=True)
    receipt = {
        "schema_version": _SCHEMA,
        "status": "VERIFIED_STAGED",
        "accepted_sha": sha,
        "source_repo": source["repo"],
        "release_repo": str(target),
        "tree": source["tree"],
        "verification": verification,
        "staged_at_utc": datetime.now(timezone.utc).isoformat(),
        "live_supervisor_changed": False,
        "authority": "STAGING_ONLY_NO_RUNTIME_DEPLOYMENT",
    }
    _atomic_json(receipt_path, receipt)
    return receipt


def readiness(receipt: dict[str, Any], *, current_sha: str | None,
              active_workers: int | None, owner_state_root: Path,
              owner_directed: bool = False) -> dict[str, Any]:
    """Read-only cutover advice. Never replaces the runtime owner/lease gates."""
    owner_allows_preparation(owner_state_root, owner_directed=owner_directed)
    validate_staged_release(receipt)
    if active_workers is None:
        return {"status": "BLOCKED_WORKER_STATE_UNKNOWN", "ready": False}
    if not isinstance(current_sha, str) or _SHA.fullmatch(current_sha) is None:
        return {"status": "BLOCKED_RUNNING_REVISION_UNKNOWN", "ready": False}
    if active_workers != 0:
        return {"status": "WAIT_FOR_WORKER_DRAIN", "ready": False, "active_workers": active_workers}
    if current_sha == receipt["accepted_sha"]:
        return {"status": "REVISION_ALREADY_LOADED", "ready": False}
    return {"status": "ELIGIBLE_FOR_CONTROLLED_CUTOVER", "ready": True,
            "accepted_sha": receipt["accepted_sha"], "current_sha": current_sha}

def observe_supervisor(state_root: Path, *, max_age_seconds: int = 120) -> dict[str, Any]:
    """Advisory health only: state files are not sufficient to authorize cutover."""
    try:
        heartbeat = json.loads((state_root / "supervisor-generation-heartbeat.json").read_text(encoding="utf-8-sig"))
        pool = json.loads((state_root / "parallel-worker-pool.json").read_text(encoding="utf-8-sig"))
        observed = datetime.fromisoformat(heartbeat["heartbeat_at_utc"].replace("Z", "+00:00"))
        pool_at = datetime.fromisoformat(pool["recorded_at_utc"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if any(date.tzinfo is None or not 0 <= (now - date).total_seconds() <= max_age_seconds
               for date in (observed, pool_at)):
            raise ValueError("heartbeat or pool data is stale")
        workers = pool["pool"]["running_total"]
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 0:
            raise ValueError("invalid worker count")
        revision = _valid_sha(heartbeat["actor_revision"])
        return {"active_workers": workers, "current_sha": revision,
                "state": "FRESH_OBSERVATION_NOT_EXECUTION_AUTHORITY"}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, ReleaseError):
        return {"active_workers": None, "current_sha": None,
                "state": "INSUFFICIENT_LIVE_EVIDENCE"}


def main(argv: list[str] | None = None) -> int:
    """Prepare and inspect; never installs or restarts a supervisor."""
    import argparse

    parser = argparse.ArgumentParser(description="Iron God local release staging controller")
    sub = parser.add_subparsers(dest="action", required=True)
    stage = sub.add_parser("stage", help="run native preflight and stage an exact commit")
    for key in ("source", "sha", "releases_root", "receipts_root", "owner_state_root"):
        stage.add_argument("--" + key.replace("_", "-"), required=True)
    stage.add_argument("--owner-directed", action="store_true")
    inspect = sub.add_parser("inspect", help="validate a staged receipt and show readiness")
    inspect.add_argument("--receipt", required=True)
    inspect.add_argument("--state-root", required=True)
    inspect.add_argument("--owner-state-root", required=True)
    inspect.add_argument("--owner-directed", action="store_true")
    try:
        args = parser.parse_args(argv)
        if args.action == "stage":
            result = stage_local_release(
                Path(args.source), args.sha, Path(args.releases_root),
                Path(args.receipts_root), Path(args.owner_state_root),
                owner_directed=args.owner_directed,
            )
        else:
            receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8-sig"))
            observations = observe_supervisor(Path(args.state_root))
            result = {**observations, **readiness(
                receipt, current_sha=observations["current_sha"],
                active_workers=observations["active_workers"],
                owner_state_root=Path(args.owner_state_root),
                owner_directed=args.owner_directed,
            )}
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, ReleaseError) as exc:
        print(json.dumps({"status": "RELEASE_REFUSED", "reason": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
