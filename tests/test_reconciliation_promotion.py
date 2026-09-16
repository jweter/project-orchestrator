from project_orchestrator.reconciliation_promotion import evaluate_promotion, reconcile_packet

SHA_A = "a" * 40
SHA_B = "b" * 40


def packet(**overrides):
    value = {
        "packet_id": "packet-1",
        "repository": "jweter/knowledge-engine-ai",
        "branch": "orchestrator/report-fix",
        "base_sha": SHA_A,
        "exact_head_sha": SHA_B,
        "state": "PREFLIGHT_GREEN",
        "changed_paths": ["src/report.py"],
    }
    value.update(overrides)
    return value


def live(**overrides):
    value = {
        "repository": "jweter/knowledge-engine-ai",
        "default_branch": "main",
        "default_head_sha": SHA_A,
        "branch": "orchestrator/report-fix",
        "branch_head_sha": SHA_B,
        "ahead_by": 1,
        "behind_by": 0,
        "changed_files": ["src/report.py"],
        "open_pr": None,
        "actively_owned": False,
        "roadmap_authorized": True,
        "contract_uncertain": False,
        "overlap_detected": False,
    }
    value.update(overrides)
    return value


def test_clean_exact_head_reaches_promotion_ready():
    result = reconcile_packet(packet(), live())
    assert result["state"] == "PROMOTION_READY"
    assert result["merge_allowed"] is False


def test_stale_default_becomes_reconciliation_lane():
    result = reconcile_packet(packet(), live(default_head_sha="c" * 40, behind_by=1))
    assert result["state"] == "RECONCILE_REQUIRED"
    assert result["reason"] == "DEFAULT_HEAD_DIVERGED"


def test_overlap_fails_closed_before_promotion():
    result = reconcile_packet(packet(), live(overlap_detected=True))
    assert result["state"] == "RECONCILE_REQUIRED"


def test_knowledge_engine_contract_uncertainty_fails_closed():
    result = reconcile_packet(packet(), live(contract_uncertain=True))
    assert result["reason"] == "OVERLAP_OR_CONTRACT_UNCERTAINTY"


def test_worker_head_movement_requires_reconciliation():
    result = reconcile_packet(packet(), live(branch_head_sha="d" * 40))
    assert result["state"] == "RECONCILE_REQUIRED"
    assert result["reason"] == "WORKER_HEAD_MOVED"


def promotion_packet(**overrides):
    value = packet(state="PROMOTION_READY")
    value.update(overrides)
    return value


def promotion_evidence(**overrides):
    value = {
        "head_sha": SHA_B,
        "verified_head_sha": SHA_B,
        "ci_status": "completed",
        "ci_conclusion": "success",
        "mergeable": True,
        "required_reviews_satisfied": True,
        "unresolved_review_threads": 0,
        "lease_valid": True,
        "policy_clear": True,
    }
    value.update(overrides)
    return value


def test_fresh_green_evidence_reaches_central_merge_revalidation_only():
    result = evaluate_promotion(promotion_packet(), promotion_evidence())
    assert result["state"] == "CENTRAL_MERGE_REVALIDATION_READY"
    assert result["merge_allowed"] is False
    assert result["next_event"] == "central_merge_revalidation"


def test_pending_ci_stays_pending():
    result = evaluate_promotion(promotion_packet(), promotion_evidence(ci_status="in_progress", ci_conclusion=None))
    assert result["state"] == "CI_PENDING"


def test_head_movement_invalidates_promotion():
    result = evaluate_promotion(promotion_packet(), promotion_evidence(head_sha="c" * 40, verified_head_sha=None))
    assert result["state"] == "RECONCILE_REQUIRED"
    assert result["recovery"]["preflight_reuse_allowed"] is False


def test_review_thread_blocks_merge_revalidation():
    result = evaluate_promotion(promotion_packet(), promotion_evidence(unresolved_review_threads=1))
    assert result["state"] == "BLOCKED"
    assert result["reason"] == "UNRESOLVED_REVIEW_THREADS"


def test_policy_gate_blocks_merge_revalidation():
    result = evaluate_promotion(promotion_packet(), promotion_evidence(policy_clear=False))
    assert result["state"] == "BLOCKED"
    assert result["reason"] == "POLICY_GATE_NOT_CLEAR"
