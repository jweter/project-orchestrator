"""Constrain a reviewed local repair to the EXISTING owner-roadmap admission path.

A completed advisory receipt does not authorize execution. This module
requires an independently verified new owner-approved capability-graph slice
and an independently observed red test before returning an *inert* handoff.
It never imports to the backlog, writes to a repository or dispatches work.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from . import iron_god_backlog as backlog
from . import iron_god_roadmap_refill as refill

_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def qualification(
    review_package: dict[str, Any],
    owner_request: dict[str, Any],
    authored_draft: dict[str, Any],
    *,
    actual_head: str,
    verified_roadmaps: dict[str, set[str]],
    items: list[dict[str, Any]],
    reserved: set[str],
    state: dict[str, Any],
    changed_since: Callable[[str, str], set[str]],
    verify_owner_approval: Callable[[dict[str, Any], dict[str, Any]], bool],
    verify_red_at_head: Callable[[str, str, str], dict[str, Any] | None],
    verify_canonical_exhaustion: Callable[[dict[str, Any]], bool],
    max_level: int = 2,
) -> dict[str, Any]:
    """Return a *non-dispatchable* exact-head handoff, or a specific refusal."""

    def deny(reason: str) -> dict[str, Any]:
        return {"status": reason, "ready_for_admission": False}

    if (
        not isinstance(review_package, dict)
        or review_package.get("advisory_only") is not True
        or review_package.get("review_required") is not True
        or review_package.get("dispatchable") is not False
        or review_package.get("attempt_budget_reset") is not False
        or review_package.get("ready_for_backlog_import") is not False
        or review_package.get("ready_for_automatic_roadmap_promotion") is not False
        or not isinstance(review_package.get("review_item"), dict)
        or not isinstance(owner_request, dict)
        or not isinstance(authored_draft, dict)
    ):
        return deny("INVALID_REVIEW_PACKAGE")
    original = review_package["review_item"]
    origin = original.get("repair_origin")
    if (
        not isinstance(origin, dict)
        or origin.get("lineage_attempts_remain_spent") is not True
        or any(
            not isinstance(origin.get(key), str) or not origin[key]
            for key in ("parent_id", "failed_key", "failed_attempt_id", "advice_sha256")
        )
        or not isinstance(origin.get("already_completed_keys"), list)
        or not isinstance(original.get("interfaces"), list)
        or not isinstance(original.get("tests"), list)
        or any(
            type(path) is not str
            for path in [*original["interfaces"], *original["tests"]]
        )
        or type(original.get("level")) is not int
    ):
        return deny("ORIGINAL_LINEAGE_NOT_FROZEN")
    repo = original.get("repository")
    if (
        repo != owner_request.get("repository")
        or repo != authored_draft.get("repository")
        or not isinstance(repo, str)
        or not _SHA.fullmatch(actual_head)
        or owner_request.get("exact_head") != actual_head
        or review_package.get("advice_sha256") != origin.get("advice_sha256")
    ):
        return deny("REPOSITORY_OR_EXACT_HEAD_MISMATCH")

    new_ref = owner_request.get("roadmap_ref")
    match = backlog.ROADMAP_REF.match(new_ref) if isinstance(new_ref, str) else None
    if (
        match is None
        or match.group(1) not in verified_roadmaps.get(repo, set())
        or new_ref.startswith("REPAIR_REVIEW:")
    ):
        return deny("NEW_OWNER_APPROVED_ROADMAP_NOT_VERIFIED")
    cap, slice_id = owner_request.get("capability"), owner_request.get("slice")
    if (
        not isinstance(cap, str)
        or not isinstance(slice_id, str)
        or owner_request.get("id") != refill.item_id(repo, cap, slice_id)
        or owner_request.get("id")
        in {row.get("id") for row in items if isinstance(row, dict)}
        or owner_request.get("id") == origin.get("failed_attempt_id")
    ):
        return deny("NEW_SLICE_IDENTITY_NOT_IN_OWNER_GRAPH")
    repair_binding = owner_request.get("repair_review")
    if not isinstance(repair_binding, dict) or any(
        repair_binding.get(k) != expected
        for k, expected in {
            "advice_sha256": origin["advice_sha256"],
            "original_parent_id": origin["parent_id"],
            "failed_key": origin["failed_key"],
            "failed_attempt_id": origin["failed_attempt_id"],
        }.items()
    ):
        return deny("REPAIR_PROVENANCE_NOT_OWNER_VERIFIED")

    allowed = owner_request.get("allowed_paths")
    original_paths = {
        *original.get("interfaces", []),
        *original.get("tests", []),
    }
    criteria = original.get("acceptance_criteria")
    if (
        not isinstance(allowed, list)
        or not allowed
        or any(type(path) is not str or path not in original_paths for path in allowed)
        or not isinstance(criteria, list)
        or not criteria
        or owner_request.get("slice_acceptance") != criteria
        # The existing draft admission validator does not compare acceptance
        # lists. Preserve the frozen child-owned contract explicitly here.
        or authored_draft.get("acceptance_criteria") != criteria
        or type(owner_request.get("capability_level")) is not int
        or owner_request["capability_level"] < original.get("level", 99)
        or not isinstance(owner_request.get("route"), dict)
        or owner_request["route"].get("builder") != refill.LOCAL
    ):
        return deny("REPAIR_BROADENS_OR_DOWNGRADES_ORIGINAL_SCOPE")
    if not isinstance(state, dict) or not isinstance(state.get("items"), dict):
        return deny("CANONICAL_ATTEMPT_LEDGER_UNAVAILABLE")
    try:
        fully_exhausted = verify_canonical_exhaustion(origin)
    except (OSError, KeyError, ValueError, TypeError):
        fully_exhausted = False
    if fully_exhausted is not True:
        return deny("CANONICAL_EXHAUSTION_NOT_INDEPENDENTLY_VERIFIED")
    failed = state["items"].get(origin["failed_attempt_id"])
    if (
        not isinstance(failed, dict)
        or not str(failed.get("outcome") or "").startswith("FAIL")
        or not isinstance(failed.get("attempts"), int)
        or failed["attempts"] < 1
    ):
        return deny("EXHAUSTED_LINEAGE_NOT_PRESERVED")
    try:
        independently_approved = verify_owner_approval(owner_request, review_package)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        independently_approved = False
    if independently_approved is not True:
        return deny("SEPARATE_OWNER_GRAPH_REVIEW_REQUIRED")
    if authored_draft.get("status") == "approved" or authored_draft.get("assignee"):
        return deny("DRAFT_CANNOT_GRANT_ITS_OWN_AUTHORITY")

    try:
        problems = refill.draft_problems(
            authored_draft,
            owner_request,
            items=items,
            reserved=reserved,
            head=actual_head,
            changed_since=changed_since,
            max_level=max_level,
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return deny("ADMISSION_INPUT_UNVERIFIABLE")
    if problems:
        return {
            "status": "EXISTING_ADMISSION_REJECTED",
            "problems": problems,
            "ready_for_admission": False,
        }
    if authored_draft.get("level") == 2:
        direct = authored_draft.get("direct")
        if not isinstance(direct, dict):
            return deny("RED_TEST_ORACLE_MISSING")
        source = direct.get("test_source")
        base = authored_draft.get("approved_base")
        if not isinstance(source, str) or base != actual_head:
            return deny("RED_TEST_BASE_NOT_EXACT_HEAD")
        try:
            proof = verify_red_at_head(
                actual_head, str(direct.get("test_path")), source
            )
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            proof = None
        if (
            not isinstance(proof, dict)
            or proof.get("head") != actual_head
            or proof.get("test_source_sha256") != _digest(source)
            or proof.get("baseline_passed") is not False
            or proof.get("native_runner_executed") is not True
            or proof.get("failed_for_assertion") is not True
            or proof.get("test_file") != direct.get("test_path")
        ):
            return deny("INDEPENDENT_RED_TEST_NOT_PROVEN")

    return {
        "status": "READY_FOR_EXISTING_ADMISSION_REVIEW",
        # It has not been admitted into the canonical runtime store.
        "ready_for_admission": False,
        "eligible_for_existing_admission_review": True,
        "dispatchable": False,
        "owner_approval_still_required_for_execution": True,
        "attempt_budget_reset": False,
        "original_failed_attempt_id": origin["failed_attempt_id"],
        "original_completed_siblings": list(origin.get("already_completed_keys") or []),
        "original_advice_sha256": origin["advice_sha256"],
        "source_roadmap_ref": new_ref,
        "exact_head": actual_head,
        "request": json.loads(json.dumps(owner_request)),
        "draft": json.loads(json.dumps(authored_draft)),
        "admission_pipeline": "iron_god_roadmap_refill.process_drafts",
    }
