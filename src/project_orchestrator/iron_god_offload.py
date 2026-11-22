"""Advisory, one-slice ChatGPT offload and lossless patch staging.

Issue #747. No model is launched; no new scheduler or execution grant exists.
The existing MCP SQLite mailbox stores only proposals, reservations and receipts.
The actual desktop owner controller, worker lease, native preflight and GitHub
review/merge gates remain separate mandatory authorities.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from . import mcp_coordination as coordination
from .iron_god_observability import REPOSITORIES

MAX_PATCH_BYTES = 262_144
MAX_PATHS = 16
_REPO = frozenset(REPOSITORIES)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_PATH = re.compile(r"^[A-Za-z0-9_./-]{1,180}$")
_DIFF = re.compile(r"^diff --git a/([^\s]+) b/([^\s]+)$")
_BLOCKED_ROOTS = frozenset({
    ".git", ".github", "engineering", "docs", "AGENTS.md", "CLAUDE.md",
    "DoWork.txt", "LICENSE", "SECURITY.md",
})
_BAD_DIFF_LINES = (
    "GIT binary patch", "Binary files ", "rename from ", "rename to ",
    "copy from ", "copy to ", "new file mode 120000",
    "deleted file mode ", "old mode ", "new mode 120000",
    "Subproject commit ",
)


class OffloadRejected(ValueError):
    """Unverified or conflicting handoff; do not proceed with code mutation."""


def _allowed_path(raw: str) -> str:
    if not isinstance(raw, str) or not _PATH.fullmatch(raw):
        raise OffloadRejected("INVALID_CHANGED_PATH")
    parts = raw.split("/")
    if len(parts) < 2 or any(part in ("", ".", "..") for part in parts):
        raise OffloadRejected("INVALID_CHANGED_PATH")
    if parts[0] in _BLOCKED_ROOTS or raw.startswith("."):
        raise OffloadRejected("PROTECTED_CHANGED_PATH")
    return raw


def _offer_fields(repository: str, slice_id: str, base_sha: str,
                  allowed_paths: list[str], authority_ref: str,
                  failure_ref: str) -> dict[str, Any]:
    if repository not in _REPO:
        raise OffloadRejected("REPOSITORY_NOT_REGISTERED")
    if not isinstance(slice_id, str) or not _TOKEN.fullmatch(slice_id):
        raise OffloadRejected("INVALID_SLICE_ID")
    if not isinstance(base_sha, str) or not _SHA.fullmatch(base_sha):
        raise OffloadRejected("EXACT_BASE_SHA_REQUIRED")
    if (not isinstance(allowed_paths, list) or not 1 <= len(allowed_paths) <= MAX_PATHS
            or any(not isinstance(p, str) for p in allowed_paths)):
        raise OffloadRejected("EXPLICIT_PATH_FENCE_REQUIRED")
    paths = sorted({_allowed_path(p) for p in allowed_paths})
    if len(paths) != len(allowed_paths):
        raise OffloadRejected("DUPLICATE_ALLOWED_PATH")
    for ref in (authority_ref, failure_ref):
        if not isinstance(ref, str) or not ref or not coordination._REFERENCE.fullmatch(ref):
            raise OffloadRejected("EXACT_AUTHORITY_AND_FAILURE_REFS_REQUIRED")
    identity = json.dumps([repository, slice_id, base_sha], separators=(",", ":"))
    return {
        "offer_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "repository": repository,
        "slice_id": slice_id,
        "base_sha": base_sha,
        "allowed_paths": paths,
        "authority_ref": authority_ref,
        "failure_ref": failure_ref,
    }


def _schema(db_path: Path) -> None:
    """Add tables to the existing advisory SQLite store, not to a lease database."""
    coordination.initialize(db_path)
    with closing(coordination._connect(db_path)) as db:
        db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS offload_offers (
                offer_id TEXT PRIMARY KEY, body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS offload_claims (
                offer_id TEXT PRIMARY KEY REFERENCES offload_offers(offer_id),
                claimer TEXT NOT NULL, claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS offload_candidates (
                offer_id TEXT PRIMARY KEY REFERENCES offload_claims(offer_id),
                patch_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
                artifact_ref TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TRIGGER IF NOT EXISTS offload_offers_immutable_update
                BEFORE UPDATE ON offload_offers
                BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_OFFLOAD_OFFER'); END;
            CREATE TRIGGER IF NOT EXISTS offload_offers_immutable_delete
                BEFORE DELETE ON offload_offers
                BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_OFFLOAD_OFFER'); END;
            CREATE TRIGGER IF NOT EXISTS offload_claims_immutable_update
                BEFORE UPDATE ON offload_claims
                BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_OFFLOAD_CLAIM'); END;
            CREATE TRIGGER IF NOT EXISTS offload_claims_immutable_delete
                BEFORE DELETE ON offload_claims
                BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_OFFLOAD_CLAIM'); END;
            CREATE TRIGGER IF NOT EXISTS offload_candidates_immutable_update
                BEFORE UPDATE ON offload_candidates
                BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_OFFLOAD_CANDIDATE'); END;
            CREATE TRIGGER IF NOT EXISTS offload_candidates_immutable_delete
                BEFORE DELETE ON offload_candidates
                BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_OFFLOAD_CANDIDATE'); END;
            COMMIT;
        """)


def offer_slice(db_path: Path, *, repository: str, slice_id: str, base_sha: str,
                allowed_paths: list[str], authority_ref: str, failure_ref: str) -> dict[str, Any]:
    """Register an inert proposal; source authenticity and authority remain unverified."""
    item = _offer_fields(repository, slice_id, base_sha, allowed_paths,
                         authority_ref, failure_ref)
    body = json.dumps(item, sort_keys=True, separators=(",", ":"))
    _schema(db_path)
    with closing(coordination._connect(db_path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            current = db.execute("SELECT body FROM offload_offers WHERE offer_id=?",
                                 (item["offer_id"],)).fetchone()
            if current is None:
                db.execute("INSERT INTO offload_offers (offer_id,body) VALUES (?,?)",
                           (item["offer_id"], body))
            elif current["body"] != body:
                raise OffloadRejected("CONFLICTING_OFFER_REPLAY")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return {**item, "authority": "ADVISORY_ONLY", "execution_authorized": False}


def read_offer(db_path: Path, offer_id: str) -> dict[str, Any]:
    """Read-only lookup never creates a missing database or claims authority."""
    if not isinstance(offer_id, str) or not re.fullmatch(r"[0-9a-f]{64}", offer_id):
        raise OffloadRejected("INVALID_OFFER_ID")
    path = coordination._path(db_path)
    if not path.is_file() or path.is_symlink():
        raise OffloadRejected("OFFER_DATABASE_UNAVAILABLE")
    with closing(coordination._connect(path, read_only=True)) as db:
        if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='offload_offers'").fetchone() is None:
            raise OffloadRejected("OFFER_DATABASE_UNAVAILABLE")
        row = db.execute("SELECT body FROM offload_offers WHERE offer_id=?", (offer_id,)).fetchone()
    if row is None:
        raise OffloadRejected("OFFER_NOT_FOUND")
    try:
        data = json.loads(row["body"])
        rebuilt = _offer_fields(data["repository"], data["slice_id"], data["base_sha"],
                                data["allowed_paths"], data["authority_ref"], data["failure_ref"])
    except (KeyError, ValueError, TypeError) as exc:
        raise OffloadRejected("OFFER_EVIDENCE_CORRUPT") from exc
    if rebuilt != data or rebuilt["offer_id"] != offer_id:
        raise OffloadRejected("OFFER_EVIDENCE_CORRUPT")
    return {**rebuilt, "authority": "ADVISORY_ONLY", "execution_authorized": False}


def claim_slice(db_path: Path, offer_id: str, *, claimer: str = "chatgpt") -> dict[str, Any]:
    """One durable ChatGPT reservation per exact slice; NEVER a worker lease."""
    if claimer != "chatgpt":
        raise OffloadRejected("ONLY_CHATGPT_OFFLOAD_RESERVATIONS")
    offer = read_offer(db_path, offer_id)
    with closing(coordination._connect(db_path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            current = db.execute("SELECT claimer FROM offload_claims WHERE offer_id=?",
                                 (offer_id,)).fetchone()
            if current is None:
                db.execute("INSERT INTO offload_claims (offer_id,claimer) VALUES (?,?)",
                           (offer_id, claimer))
                replayed = False
            elif current["claimer"] == claimer:
                replayed = True
            else:
                raise OffloadRejected("OFFLOAD_ALREADY_RESERVED")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return {"offer_id": offer["offer_id"], "claimer": claimer,
            "replayed": replayed, "worker_lease_granted": False}


def changed_paths(patch: bytes) -> list[str]:
    """Accept text-only, non-renaming Git diffs and exact fenced paths."""
    if not isinstance(patch, bytes) or not 1 <= len(patch) <= MAX_PATCH_BYTES or b"\x00" in patch:
        raise OffloadRejected("PATCH_SIZE_OR_ENCODING_REFUSED")
    try:
        lines = patch.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise OffloadRejected("PATCH_SIZE_OR_ENCODING_REFUSED") from exc
    paths: list[str] = []
    for line in lines:
        if line.startswith("diff --git "):
            match = _DIFF.fullmatch(line)
            if match is None or match.group(1) != match.group(2):
                raise OffloadRejected("UNSAFE_PATCH_HEADER")
            paths.append(_allowed_path(match.group(1)))
        elif line.startswith(_BAD_DIFF_LINES):
            raise OffloadRejected("UNSUPPORTED_DESTRUCTIVE_OR_BINARY_PATCH")
        elif line.startswith("--- ") and line != "--- /dev/null":
            if not line.startswith("--- a/") or _allowed_path(line[6:]) not in paths:
                raise OffloadRejected("UNSAFE_PATCH_HEADER")
        elif line.startswith("+++ ") and line != "+++ /dev/null":
            if not line.startswith("+++ b/") or _allowed_path(line[6:]) not in paths:
                raise OffloadRejected("UNSAFE_PATCH_HEADER")
    if not paths or len(paths) != len(set(paths)) or len(paths) > MAX_PATHS:
        raise OffloadRejected("PATCH_CHANGED_PATHS_UNVERIFIED")
    return sorted(paths)


def _write_immutable(path: Path, data: bytes) -> None:
    """Idempotently persist bytes; never overwrite or follow a pre-existing link."""
    if path.is_symlink():
        raise OffloadRejected("ARTIFACT_LINK_REFUSED")
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise OffloadRejected("IMMUTABLE_ARTIFACT_COLLISION") from None


def seal_candidate(offer: dict[str, Any], patch: bytes, stage_root: Path) -> dict[str, Any]:
    """Stage immutable bytes and digest-verified manifest; no code execution."""
    paths = changed_paths(patch)
    if not set(paths) <= set(offer["allowed_paths"]):
        raise OffloadRejected("PATCH_EXCEEDS_APPROVED_PATH_FENCE")
    offer_id = offer["offer_id"]
    if not isinstance(offer_id, str) or not re.fullmatch(r"[0-9a-f]{64}", offer_id):
        raise OffloadRejected("INVALID_OFFER_ID")
    root = Path(stage_root)
    if not root.is_absolute() or root.is_symlink():
        raise OffloadRejected("ABSOLUTE_NON_LINK_STAGING_REQUIRED")
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(patch).hexdigest()
    patch_name = f"{offer_id}-{digest}.patch"
    manifest = {
        "schema_version": 1, "offer_id": offer_id,
        "repository": offer["repository"], "base_sha": offer["base_sha"],
        "patch_file": patch_name, "patch_sha256": digest,
        "changed_paths": paths, "status": "CANDIDATE_NOT_VERIFIED",
    }
    data = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    manifest_name = f"{offer_id}-{digest}.json"
    _write_immutable(root / patch_name, patch)
    _write_immutable(root / manifest_name, data)
    return {
        "offer_id": offer_id, "patch_sha256": digest,
        "manifest_sha256": hashlib.sha256(data).hexdigest(),
        "manifest_path": str(root / manifest_name), "changed_paths": paths,
        "native_preflight": "NOT_RUN", "execution_authorized": False,
    }


def register_candidate(db_path: Path, candidate: dict[str, Any]) -> dict[str, Any]:
    """Persist an immutable receipt, never asserting the code passed tests."""
    oid = candidate["offer_id"]
    read_offer(db_path, oid)
    sha, manifest_sha = candidate["patch_sha256"], candidate["manifest_sha256"]
    if any(not isinstance(x, str) or not re.fullmatch(r"[0-9a-f]{64}", x)
           for x in (sha, manifest_sha)):
        raise OffloadRejected("INVALID_CANDIDATE_HASH")
    ref = candidate["manifest_path"]
    if not isinstance(ref, str) or not Path(ref).is_absolute():
        raise OffloadRejected("ABSOLUTE_ARTIFACT_REFERENCE_REQUIRED")
    with closing(coordination._connect(db_path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("SELECT 1 FROM offload_claims WHERE offer_id=? AND claimer='chatgpt'",
                          (oid,)).fetchone() is None:
                raise OffloadRejected("CHATGPT_RESERVATION_REQUIRED")
            current = db.execute("SELECT * FROM offload_candidates WHERE offer_id=?",
                                 (oid,)).fetchone()
            expected = (sha, manifest_sha, ref)
            if current is None:
                db.execute("INSERT INTO offload_candidates "
                           "(offer_id,patch_sha256,manifest_sha256,artifact_ref) VALUES (?,?,?,?)",
                           (oid, *expected))
            elif tuple(current[x] for x in ("patch_sha256", "manifest_sha256", "artifact_ref")) != expected:
                raise OffloadRejected("CANDIDATE_REPLAY_CONFLICT")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return {"offer_id": oid, "patch_sha256": sha, "status": "STAGED_UNVERIFIED",
            "execution_authorized": False}


def verify_candidate(offer: dict[str, Any], candidate: dict[str, Any]) -> bytes:
    """Read and verify the exact staged bytes without relying on a claim of PASS."""
    manifest_path = Path(candidate["manifest_path"])
    if not manifest_path.is_absolute() or manifest_path.is_symlink():
        raise OffloadRejected("UNSAFE_MANIFEST_PATH")
    data = manifest_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != candidate["manifest_sha256"]:
        raise OffloadRejected("MANIFEST_HASH_MISMATCH")
    try:
        manifest = json.loads(data)
    except ValueError as exc:
        raise OffloadRejected("INVALID_MANIFEST") from exc
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("offer_id") != offer["offer_id"]
            or manifest.get("repository") != offer["repository"]
            or manifest.get("base_sha") != offer["base_sha"]
            or manifest.get("patch_sha256") != candidate["patch_sha256"]
            or manifest.get("status") != "CANDIDATE_NOT_VERIFIED"):
        raise OffloadRejected("MANIFEST_IDENTITY_MISMATCH")
    name = manifest.get("patch_file")
    if name != f"{offer['offer_id']}-{candidate['patch_sha256']}.patch":
        raise OffloadRejected("UNSAFE_ARTIFACT_REFERENCE")
    patch_file = manifest_path.parent / name
    if patch_file.is_symlink() or not patch_file.is_file():
        raise OffloadRejected("PATCH_MISSING_OR_LINKED")
    patch = patch_file.read_bytes()
    if hashlib.sha256(patch).hexdigest() != candidate["patch_sha256"]:
        raise OffloadRejected("PATCH_HASH_MISMATCH")
    paths = changed_paths(patch)
    if paths != manifest.get("changed_paths") or not set(paths) <= set(offer["allowed_paths"]):
        raise OffloadRejected("PATCH_PATHS_CHANGED")
    return patch


def _git(path: Path, *args: str, input_data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(path), *args], input=input_data,
                          capture_output=True, check=False, timeout=90)


def prepare_isolated_import(
    offer: dict[str, Any], candidate: dict[str, Any], *,
    clone: Path, destination: Path,
    authority_check: Callable[[], bool] | None = None,
    ownership_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Apply only after external authenticated owner/lease checks; never commit/push.

    This is deliberately an opt-in importer rather than an executable MCP tool.
    The owner-controlled product lane must invoke its own native preflight and
    normal exact-head publication after inspecting the resulting worktree.
    """
    patch = verify_candidate(offer, candidate)
    if authority_check is None or ownership_check is None:
        raise OffloadRejected("INDEPENDENT_OWNER_AND_LEASE_GATES_REQUIRED")
    if authority_check() is not True or ownership_check() is not True:
        raise OffloadRejected("OWNER_OR_WORKER_OWNERSHIP_DENIED")
    repo, tree = Path(clone), Path(destination)
    if (not repo.is_dir() or repo.is_symlink() or not tree.is_absolute()
            or tree.exists() or tree.is_symlink() or not tree.parent.is_dir()):
        raise OffloadRejected("ISOLATED_WORKTREE_REQUIRED")
    base_sha = offer["base_sha"]
    base = _git(repo, "cat-file", "-t", base_sha)
    if base.returncode or base.stdout.strip() != b"commit":
        raise OffloadRejected("BASE_COMMIT_NOT_PRESENT")
    if authority_check() is not True or ownership_check() is not True:
        raise OffloadRejected("OWNER_OR_WORKER_OWNERSHIP_DENIED")
    add = _git(repo, "worktree", "add", "--detach", "--", str(tree), base_sha)
    if add.returncode:
        raise OffloadRejected("WORKTREE_CREATION_FAILED")
    head = _git(tree, "rev-parse", "HEAD")
    if head.returncode or head.stdout.decode().strip() != base_sha:
        raise OffloadRejected("ISOLATED_HEAD_MISMATCH")
    if authority_check() is not True or ownership_check() is not True:
        raise OffloadRejected("OWNER_OR_WORKER_OWNERSHIP_DENIED")
    checked = _git(tree, "apply", "--check", "--index", "-", input_data=patch)
    if checked.returncode:
        raise OffloadRejected("GIT_APPLY_CHECK_FAILED")
    applied = _git(tree, "apply", "--index", "-", input_data=patch)
    if applied.returncode:
        raise OffloadRejected("GIT_APPLY_FAILED")
    actual = _git(tree, "diff", "--cached", "--name-only", "-z")
    if actual.returncode:
        raise OffloadRejected("CHANGED_PATH_VERIFICATION_FAILED")
    paths = sorted(x for x in actual.stdout.decode("utf-8").split("\x00") if x)
    if paths != changed_paths(patch) or not set(paths) <= set(offer["allowed_paths"]):
        raise OffloadRejected("POST_APPLY_PATH_CONFLICT")
    return {
        "status": "PREFLIGHT_UNVERIFIED",
        "worktree": str(tree), "base_sha": base_sha,
        "changed_paths": paths, "patch_sha256": candidate["patch_sha256"],
        "next_gate": "REPOSITORY_NATIVE_PREFLIGHT_THEN_EXISTING_PUBLICATION_LANE",
        "merge_authorized": False, "product_reality_verified": False,
    }
