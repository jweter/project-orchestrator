"""Bounded GitHub-branch patch ingestion for the existing advisory offload lane.

This is a local, on-demand transport adapter: cloud DoWork can publish a
one-commit branch using the GitHub connector without paying for desktop calls.
An authenticated local controller must first fetch that branch from its
configured repository and independently verify current owner, head, worker
lease and file ownership. Neither Git objects nor this adapter grant those.
No network request, worker launch, code execution, PR, merge or release occurs.
"""
from __future__ import annotations

import re
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any

from . import iron_god_offload as offload

_HEAD = re.compile(r"^[0-9a-f]{40}$")


def _git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            # refs/replace and GIT_REPLACE_REF_BASE may otherwise rewrite
            # commit parents/tree contents beneath a trusted remote SHA.
            ["git", "--no-replace-objects", "-C", str(repo), *args],
            capture_output=True, check=False, timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise offload.OffloadRejected("GIT_EVIDENCE_UNAVAILABLE") from exc
    if result.returncode:
        raise offload.OffloadRejected("GIT_EVIDENCE_UNAVAILABLE")
    return result.stdout


def stage_fetched_github_candidate(
    *, db_path: Path, stage_root: Path, repository_checkout: Path,
    offer_id: str, candidate_head: str, observed_base_head: str,
) -> dict[str, Any]:
    """Seal one fetched proposal commit, without authorizing import or execution.

    Caller MUST fetch only the configured GitHub repository's exact
    refs/heads/offload/chatgpt/<offer_id> to its local origin-tracking ref,
    verify live GitHub path ownership, and validate the authenticated owner
    session and worker-lease gate separately. A local Git remote-tracking ref
    alone is not a trusted GitHub or owner identity.
    """
    offer = offload.read_offer(db_path, offer_id)
    if (not isinstance(candidate_head, str) or not _HEAD.fullmatch(candidate_head)
            or not isinstance(observed_base_head, str)
            or not _HEAD.fullmatch(observed_base_head)):
        raise offload.OffloadRejected("EXACT_GITHUB_HEADS_REQUIRED")
    if observed_base_head != offer["base_sha"]:
        raise offload.OffloadRejected("STALE_GITHUB_BASE_HEAD")

    # An existing immutable local ChatGPT advisory reservation is necessary.
    # Never fabricate the claim from untrusted branch metadata.
    with closing(offload.coordination._connect(db_path, read_only=True)) as db:
        row = db.execute(
            "SELECT claimer FROM offload_claims WHERE offer_id=?",
            (offer_id,),
        ).fetchone()
    if row is None or row["claimer"] != "chatgpt":
        raise offload.OffloadRejected("CHATGPT_ADVISORY_RESERVATION_REQUIRED")

    repo = Path(repository_checkout)
    if not repo.is_dir() or repo.is_symlink():
        raise offload.OffloadRejected("ISOLATED_REPOSITORY_CHECKOUT_REQUIRED")
    ref = f"refs/remotes/origin/offload/chatgpt/{offer_id}"
    # The local adapter cannot assert live network authenticity; exact-ref
    # verification only prevents importing a different local commit by mistake.
    ref_sha = _git(repo, "rev-parse", "--verify", ref).decode("ascii").strip()
    if ref_sha != candidate_head:
        raise offload.OffloadRejected("FETCHED_BRANCH_HEAD_CHANGED")
    if _git(repo, "cat-file", "-t", observed_base_head).strip() != b"commit":
        raise offload.OffloadRejected("BASE_COMMIT_NOT_AVAILABLE")
    if _git(repo, "cat-file", "-t", candidate_head).strip() != b"commit":
        raise offload.OffloadRejected("CANDIDATE_COMMIT_NOT_AVAILABLE")
    lineage = _git(repo, "rev-list", "--parents", "-n", "1", candidate_head).split()
    if lineage != [candidate_head.encode("ascii"), observed_base_head.encode("ascii")]:
        raise offload.OffloadRejected("SINGLE_EXACT_BASE_PARENT_REQUIRED")

    # Diff ALL paths, not an allowed-path filter (which could hide injected
    # protected changes). Existing patch parser blocks binary, deletions,
    # renames, symlinks, submodules and unsafe metadata.
    patch = _git(
        repo, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
        "--ignore-submodules=none", observed_base_head, candidate_head, "--",
    )
    paths = offload.changed_paths(patch)
    if not set(paths).issubset(offer["allowed_paths"]):
        raise offload.OffloadRejected("GITHUB_BRANCH_PATH_FENCE_VIOLATION")
    candidate = offload.seal_candidate(offer, patch, stage_root)
    receipt = offload.register_candidate(db_path, candidate)
    return {
        "status": receipt["status"], "offer_id": offer_id,
        "base_sha": observed_base_head, "candidate_head": candidate_head,
        "patch_sha256": candidate["patch_sha256"],
        "manifest_sha256": candidate["manifest_sha256"],
        "manifest_path": candidate["manifest_path"],
        "changed_paths": paths,
        "source": "LOCAL_FETCHED_GIT_REF_UNTRUSTED_UNTIL_EXTERNAL_VERIFICATION",
        "execution_authorized": False,
        "worker_lease_granted": False,
        "merge_authorized": False,
    }
