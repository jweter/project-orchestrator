"""Guarded producer for *advisory* ChatGPT difficult-slice offers.

This opt-in adapter composes the canonical, exact-head failure selector and
the existing immutable SQLite offer primitive. It is not an MCP tool,
scheduler, worker claim, execution controller, native preflight, or merger.
The installed resident caller must supply *independently obtained* live GitHub,
owner and worker-claim checks; untrusted mailbox text cannot provide these.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import iron_god_offload as offload
from . import iron_god_offload_eligibility as eligibility

LiveReader = Callable[[str], dict[str, Any]]
OwnerCheck = Callable[[], bool]
ConflictCheck = Callable[[str, tuple[str, ...]], bool]
_FIELDS = frozenset({
    "observed_remote_head", "measured_local_level",
    "open_pr_paths", "active_packet_id",
})


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "status": "OFFER_REFUSED",
        "reason": reason,
        "advisory_only": True,
        "execution_authorized": False,
        "worker_lease_granted": False,
        "native_acceptance_verified": False,
    }


def _view(reader: LiveReader, repository: str) -> tuple[str, int, tuple[str, ...], str | None]:
    """Get a bounded, complete, current witness without trusting arbitrary fields."""
    row = reader(repository)
    if not isinstance(row, dict) or set(row) != _FIELDS:
        raise ValueError("LIVE_GITHUB_AND_LANE_VIEW_REQUIRED")
    head = row["observed_remote_head"]
    level = row["measured_local_level"]
    paths = row["open_pr_paths"]
    packet = row["active_packet_id"]
    if (not isinstance(head, str) or not offload._SHA.fullmatch(head)
            or type(level) is not int or not 0 <= level <= 5
            or not isinstance(paths, list) or len(paths) > 512
            or any(not isinstance(p, str) or len(p) > 180 for p in paths)
            or (packet is not None and (not isinstance(packet, str)
                                        or not offload._TOKEN.fullmatch(packet)))):
        raise ValueError("LIVE_GITHUB_AND_LANE_VIEW_REQUIRED")
    return head, level, tuple(sorted(paths)), packet


def offer_verified_difficult_slice(
    *, db_path: Path, release_repo: Path, lane_root: Path, repository: str,
    read_live: LiveReader, authenticated_owner_check: OwnerCheck,
    no_conflicting_lease_check: ConflictCheck,
    advisory_writes_enabled: bool = False,
) -> dict[str, Any]:
    """Publish one inert, immutable offer only from a witnessed current failure.

    The selector matches a current-head failed worker packet and the matching
    SQLite development-memory attempt. The protected resident caller supplies
    independent owner, live GitHub PR/head, and lease/packet witnesses; no
    caller-supplied strings from a network/MCP request may be accepted as them.

    A TOCTOU change can always occur after the final observation, so even a
    successfully published row must be rechecked by the protected importer
    before claim, staging, or any source mutation.
    """
    if advisory_writes_enabled is not True:
        return _blocked("ADVISORY_WRITES_NOT_AUTHORIZED")
    if repository not in offload._REPO:
        return _blocked("REPOSITORY_UNREGISTERED")
    # These paths belong to the protected resident host; never let an
    # untrusted caller redirect an opt-in SQLite write to an arbitrary path.
    try:
        roots = (db_path, release_repo, lane_root)
        if (any(not isinstance(path, Path) or not path.is_absolute()
                or path.is_symlink() for path in roots)
                or not db_path.parent.is_dir()
                or db_path.parent.is_symlink()
                or not release_repo.is_dir()
                or not lane_root.is_dir()):
            return _blocked("CONFIGURED_HOST_ROOTS_INVALID")
    except OSError:
        return _blocked("CONFIGURED_HOST_ROOTS_INVALID")
    if (not callable(read_live) or not callable(authenticated_owner_check)
            or not callable(no_conflicting_lease_check)):
        return _blocked("INDEPENDENT_LIVE_WITNESSES_REQUIRED")
    try:
        if authenticated_owner_check() is not True:
            return _blocked("OWNER_NOT_AUTHORIZED")
        before = _view(read_live, repository)
        head, level, open_prs, active_packet = before
        candidate = eligibility.inspect_difficult_slice(
            release_repo, lane_root, repository,
            observed_remote_head=head, measured_local_level=level,
            open_pr_paths=list(open_prs), active_packet_id=active_packet,
        )
        if candidate.get("status") != "CANDIDATE_UNVERIFIED":
            return _blocked("NO_VERIFIED_CURRENT_HEAD_FAILURE")
        fields = candidate.get("candidate")
        if not isinstance(fields, dict):
            return _blocked("CANDIDATE_EVIDENCE_INVALID")
        allowed = fields.get("allowed_paths")
        if (not isinstance(allowed, list) or not allowed
                or any(not isinstance(p, str) for p in allowed)):
            return _blocked("CANDIDATE_EVIDENCE_INVALID")
        paths = tuple(sorted(allowed))
        # A free lane is NOT a granted worker lease. This callback only checks
        # for colliding existing owners, and must be independently backed by
        # the protected native worker/claim oracle.
        if no_conflicting_lease_check(repository, paths) is not True:
            return _blocked("WORKER_OR_PATH_CLAIM_CONFLICT")
        if (_view(read_live, repository) != before
                or authenticated_owner_check() is not True
                or no_conflicting_lease_check(repository, paths) is not True):
            return _blocked("LIVE_GATES_CHANGED_BEFORE_OFFER")
        offer = offload.offer_slice(
            db_path,
            repository=repository, slice_id=fields["slice_id"],
            base_sha=fields["base_sha"], allowed_paths=allowed,
            authority_ref=fields["authority_ref"],
            failure_ref=fields["failure_ref"],
        )
        # The immutable advisory row may now exist even if a gate is revoked.
        # Never imply a live claim or safe import from an issued offer ID.
        if (_view(read_live, repository) != before
                or authenticated_owner_check() is not True
                or no_conflicting_lease_check(repository, paths) is not True):
            return _blocked("OFFER_REQUIRES_REVALIDATION_AFTER_GATE_CHANGE")
    except (offload.OffloadRejected, OSError, ValueError, KeyError, TypeError):
        return _blocked("LIVE_EVIDENCE_OR_OFFER_UNAVAILABLE")
    except Exception:  # noqa: BLE001 - never trust unhandled witness callbacks
        return _blocked("LIVE_WITNESS_OR_ORACLE_FAILED")
    return {
        "status": "ADVISORY_OFFER_PUBLISHED",
        "offer_id": offer["offer_id"],
        "repository": repository,
        "slice_id": offer["slice_id"],
        "base_sha": offer["base_sha"],
        "allowed_paths": offer["allowed_paths"],
        "source": candidate["source"],
        "advisory_only": True,
        "execution_authorized": False,
        "worker_lease_granted": False,
        "native_acceptance_verified": False,
        "requires_current_owner_lease_and_head_revalidation": True,
    }
