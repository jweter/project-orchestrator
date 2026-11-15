"""Existing owner-roadmap admission still governs separately approved repairs."""

from __future__ import annotations

import copy
import hashlib

import pytest

from project_orchestrator import iron_god_microtask_repair_handoff as handoff
from project_orchestrator import iron_god_roadmap_refill as refill

REPO = "jweter/knowledge-engine-web"
HEAD = "d" * 40
SOURCE = "def test_normalize_title():\n    assert normalize_title('A') == 'a'\n"
ORIGIN = {
    "parent_id": "RM-web-original",
    "failed_key": "t1",
    "failed_attempt_id": "RM-web-original-t1-r1",
    "advice_sha256": "e" * 64,
    "already_completed_keys": ["t3"],
    "lineage_attempts_remain_spent": True,
}
ROADMAP = "docs/OWNER_APPROVED_ROADMAP_web.md"
REQUEST = {
    "id": refill.item_id(REPO, "cap-one", "repair-title"),
    "repository": REPO,
    "roadmap_ref": ROADMAP + "#cap-one",
    "capability": "cap-one",
    "slice": "repair-title",
    "exact_head": HEAD,
    "capability_level": 2,
    "route": {"builder": refill.LOCAL},
    "allowed_paths": ["pkg/one.py", "tests/test_one.py"],
    "forbidden_paths": ["pkg/secret/**"],
    "slice_acceptance": ["Preserve title normalization semantics"],
    "repair_review": {
        "advice_sha256": ORIGIN["advice_sha256"],
        "original_parent_id": ORIGIN["parent_id"],
        "failed_key": ORIGIN["failed_key"],
        "failed_attempt_id": ORIGIN["failed_attempt_id"],
    },
}
REVIEW = {
    "advisory_only": True,
    "review_required": True,
    "dispatchable": False,
    "attempt_budget_reset": False,
    "ready_for_backlog_import": False,
    "ready_for_automatic_roadmap_promotion": False,
    "advice_sha256": ORIGIN["advice_sha256"],
    "review_item": {
        "repository": REPO,
        "id": "IGR-" + ORIGIN["advice_sha256"][:20],
        "level": 2,
        "status": "proposed",
        "interfaces": ["pkg/one.py"],
        "tests": ["tests/test_one.py"],
        "acceptance_criteria": REQUEST["slice_acceptance"],
        "repair_origin": ORIGIN,
    },
}
DRAFT = {
    "id": REQUEST["id"],
    "repository": REPO,
    "status": "proposed",
    "title": "Targeted normalization fix",
    "roadmap_ref": REQUEST["roadmap_ref"],
    "addresses": "cap-one",
    "why": "Existing behavior disagrees with accepted title rules",
    "consumer": "the original existing knowledge-engine-web title display",
    "objective": "Correct title normalization without changing existing consumer",
    "interfaces": ["pkg/one.py"],
    "depends_on": [],
    "acceptance_criteria": REQUEST["slice_acceptance"],
    "tests": ["tests/test_one.py"],
    "level": 2,
    "complexity": "low",
    "verification": "Owner-approved failing test changes from red to green",
    "definition_of_done": "Merged with native product acceptance and exact-head CI",
    "approved_base": HEAD,
    "direct": {
        "module_path": "pkg/one.py",
        "test_path": "tests/test_one.py",
        "targets": ["normalize_title"],
        "direction": "Repair the existing normalize_title implementation preserving its interface",
        "test_source": SOURCE,
        "approved_base": HEAD,
    },
}
STATE = {
    "items": {
        ORIGIN["failed_attempt_id"]: {
            "outcome": "FAIL: SLICE_TESTS_FAILED",
            "attempts": 3,
            "packet_id": "PRODUCT-FAILED",
        }
    }
}


def check(review=None, request=None, draft=None, *, approval=True, red=True, **kw):
    if review is None:
        review = copy.deepcopy(REVIEW)
    if request is None:
        request = copy.deepcopy(REQUEST)
    if draft is None:
        draft = copy.deepcopy(DRAFT)
    exhausted = kw.pop("exhaustion", True)
    return handoff.qualification(
        review,
        request,
        draft,
        actual_head=kw.pop("actual_head", HEAD),
        verified_roadmaps=kw.pop("verified_roadmaps", {REPO: {ROADMAP}}),
        items=kw.pop("items", []),
        reserved=kw.pop("reserved", set()),
        state=kw.pop("state", STATE),
        changed_since=lambda *_: set(),
        verify_owner_approval=lambda *_: approval,
        verify_canonical_exhaustion=lambda *_: exhausted,
        verify_red_at_head=lambda head, filename, source: {
            "head": head,
            "test_file": filename,
            "test_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "baseline_passed": not red,
            "native_runner_executed": True,
            "failed_for_assertion": red,
        },
        **kw,
    )


def test_new_owner_graph_slice_qualifies_only_as_inert_handoff() -> None:
    before = copy.deepcopy((REVIEW, REQUEST, DRAFT, STATE))
    result = check()
    assert result["status"] == "READY_FOR_EXISTING_ADMISSION_REVIEW"
    assert result["ready_for_admission"] is False
    assert result["eligible_for_existing_admission_review"] is True
    assert result["dispatchable"] is False
    assert result["attempt_budget_reset"] is False
    assert result["owner_approval_still_required_for_execution"] is True
    assert result["admission_pipeline"] == "iron_god_roadmap_refill.process_drafts"
    assert result["original_failed_attempt_id"] == ORIGIN["failed_attempt_id"]
    assert result["original_completed_siblings"] == ["t3"]
    assert result["draft"]["id"] == REQUEST["id"]
    assert (REVIEW, REQUEST, DRAFT, STATE) == before


@pytest.mark.parametrize(
    ("alter", "reason"),
    [
        (lambda r, q, d: r.update(dispatchable=True), "INVALID_REVIEW_PACKAGE"),
        (
            lambda r, q, d: r["review_item"]["repair_origin"].update(
                lineage_attempts_remain_spent=False
            ),
            "ORIGINAL_LINEAGE_NOT_FROZEN",
        ),
        (
            lambda r, q, d: q.update(exact_head="b" * 40),
            "REPOSITORY_OR_EXACT_HEAD_MISMATCH",
        ),
        (
            lambda r, q, d: q.update(roadmap_ref="REPAIR_REVIEW:pretend"),
            "NEW_OWNER_APPROVED_ROADMAP_NOT_VERIFIED",
        ),
        (
            lambda r, q, d: q.update(id="RM-old-capped-r2"),
            "NEW_SLICE_IDENTITY_NOT_IN_OWNER_GRAPH",
        ),
        (
            lambda r, q, d: q["repair_review"].update(advice_sha256="0" * 64),
            "REPAIR_PROVENANCE_NOT_OWNER_VERIFIED",
        ),
        (
            lambda r, q, d: q.update(allowed_paths=["pkg/**"]),
            "REPAIR_BROADENS_OR_DOWNGRADES_ORIGINAL_SCOPE",
        ),
        (
            lambda r, q, d: q.update(capability_level=1),
            "REPAIR_BROADENS_OR_DOWNGRADES_ORIGINAL_SCOPE",
        ),
        (
            lambda r, q, d: q.update(route={"builder": "CAPABLE"}),
            "REPAIR_BROADENS_OR_DOWNGRADES_ORIGINAL_SCOPE",
        ),
        (
            lambda r, q, d: d.update(status="approved"),
            "DRAFT_CANNOT_GRANT_ITS_OWN_AUTHORITY",
        ),
        (
            lambda r, q, d: d.update(assignee="iron-god"),
            "DRAFT_CANNOT_GRANT_ITS_OWN_AUTHORITY",
        ),
    ],
)
def test_rejects_stale_tampered_or_self_authorized_material(alter, reason: str) -> None:
    review, request, draft = copy.deepcopy((REVIEW, REQUEST, DRAFT))
    alter(review, request, draft)
    assert check(review, request, draft)["status"] == reason


def test_new_owner_roadmap_and_independent_review_are_both_required() -> None:
    assert (
        check(verified_roadmaps={REPO: set()})["status"]
        == "NEW_OWNER_APPROVED_ROADMAP_NOT_VERIFIED"
    )
    assert check(approval=False)["status"] == "SEPARATE_OWNER_GRAPH_REVIEW_REQUIRED"


def test_existing_admission_pipeline_rejects_conflicts_duplicates_and_scope() -> None:
    assert check(reserved={"pkg/one.py"})["status"] == "EXISTING_ADMISSION_REJECTED"
    assert (
        check(items=[{"id": REQUEST["id"], "repository": REPO}])["status"]
        == "NEW_SLICE_IDENTITY_NOT_IN_OWNER_GRAPH"
    )
    draft = copy.deepcopy(DRAFT)
    draft["interfaces"].append("pkg/unknown.py")
    assert check(draft=draft)["status"] == "EXISTING_ADMISSION_REJECTED"


def test_red_test_must_be_independently_observed_at_exact_head() -> None:
    assert check(red=False)["status"] == "INDEPENDENT_RED_TEST_NOT_PROVEN"
    missing = lambda *_: None
    assert (
        handoff.qualification(
            REVIEW,
            REQUEST,
            DRAFT,
            actual_head=HEAD,
            verified_roadmaps={REPO: {ROADMAP}},
            items=[],
            reserved=set(),
            state=STATE,
            changed_since=lambda *_: set(),
            verify_owner_approval=lambda *_: True,
            verify_canonical_exhaustion=lambda *_: True,
            verify_red_at_head=missing,
        )["status"]
        == "INDEPENDENT_RED_TEST_NOT_PROVEN"
    )


def test_original_failure_ledger_is_not_erased_or_reclassified() -> None:
    state = {"items": {}}
    assert check(state=state)["status"] == "EXHAUSTED_LINEAGE_NOT_PRESERVED"
    state = copy.deepcopy(STATE)
    state["items"][ORIGIN["failed_attempt_id"]]["outcome"] = "MERGED"
    assert check(state=state)["status"] == "EXHAUSTED_LINEAGE_NOT_PRESERVED"


def test_independent_exhaustion_is_not_merely_an_advisory_claim() -> None:
    assert (
        check(exhaustion=False)["status"]
        == "CANONICAL_EXHAUSTION_NOT_INDEPENDENTLY_VERIFIED"
    )
    assert (
        check(exhaustion=1)["status"]
        == "CANONICAL_EXHAUSTION_NOT_INDEPENDENTLY_VERIFIED"
    )


def test_red_test_environment_failure_cannot_qualify_as_code_failure() -> None:
    def broken_runner(_head, _filename, source):
        return {
            "head": HEAD,
            "test_file": "tests/test_one.py",
            "test_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "baseline_passed": False,
            "native_runner_executed": True,
            "failed_for_assertion": False,
        }

    assert (
        handoff.qualification(
            REVIEW,
            REQUEST,
            DRAFT,
            actual_head=HEAD,
            verified_roadmaps={REPO: {ROADMAP}},
            items=[],
            reserved=set(),
            state=STATE,
            changed_since=lambda *_: set(),
            verify_owner_approval=lambda *_: True,
            verify_canonical_exhaustion=lambda *_: True,
            verify_red_at_head=broken_runner,
        )["status"]
        == "INDEPENDENT_RED_TEST_NOT_PROVEN"
    )


def test_untrusted_malformed_specs_and_unavailable_reviewers_fail_closed() -> None:
    review = copy.deepcopy(REVIEW)
    review["review_item"]["interfaces"] = [{"not": "a safe pathname"}]
    assert check(review=review)["status"] == "ORIGINAL_LINEAGE_NOT_FROZEN"
    request = copy.deepcopy(REQUEST)
    request["route"] = "LOCAL"
    assert (
        check(request=request)["status"]
        == "REPAIR_BROADENS_OR_DOWNGRADES_ORIGINAL_SCOPE"
    )

    def unavailable(_request, _package):
        raise OSError("owner graph cannot be independently verified")

    assert (
        handoff.qualification(
            REVIEW,
            REQUEST,
            DRAFT,
            actual_head=HEAD,
            verified_roadmaps={REPO: {ROADMAP}},
            items=[],
            reserved=set(),
            state=STATE,
            changed_since=lambda *_: set(),
            verify_owner_approval=unavailable,
            verify_canonical_exhaustion=lambda *_: True,
            verify_red_at_head=lambda *_: None,
        )["status"]
        == "SEPARATE_OWNER_GRAPH_REVIEW_REQUIRED"
    )


@pytest.mark.parametrize(
    "substitution",
    [
        ["Weaker criterion unrelated to original title normalization"],
        [],
        ["Preserve title normalization semantics", "also modify another module"],
        ["Something unrelated", "Preserve title normalization semantics"],
    ],
)
def test_repair_draft_cannot_weaken_or_extend_original_acceptance(
    substitution: list[str],
) -> None:
    draft = copy.deepcopy(DRAFT)
    draft["acceptance_criteria"] = substitution
    result = check(draft=draft)
    assert result["status"] == "REPAIR_BROADENS_OR_DOWNGRADES_ORIGINAL_SCOPE"
    assert result["ready_for_admission"] is False
    assert DRAFT["acceptance_criteria"] == REQUEST["slice_acceptance"]
