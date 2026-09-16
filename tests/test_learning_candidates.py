from __future__ import annotations

import copy

import pytest

from project_orchestrator.learning_candidates import (
    LearningCandidateError,
    build_promotion_packet,
    validate_candidate,
    validate_store,
)


def candidate() -> dict:
    return {
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-ai",
        "observation": "A report rule appeared useful in repeated evidence-boundary work.",
        "proposed_rule": "Mirror prompt-side evidence rules with deterministic validation.",
        "evidence_refs": ["knowledge-engine-ai#162", "knowledge-engine-ai#163"],
        "source_harness": "codex",
        "created_at": "2026-09-16T14:00:00Z",
        "status": "CANDIDATE",
        "authority": "NON_AUTHORITATIVE",
        "confidence": 0.8,
        "product_reality_may_be_inferred": False,
    }


def verification() -> dict:
    return {
        "verification_authority": "DETERMINISTIC_CI",
        "independent_verifier": "github-actions",
        "source_type": "success_pattern",
        "source_ref": "knowledge-engine-ai#163",
        "root_cause": "Prompt-only guidance was not an executable evidence boundary.",
        "fix": "Add deterministic validation plus regression coverage.",
        "verification": "Exact-head preflight and fresh PR CI passed.",
        "regression_protection": "Repository-native tests reject the known unsafe shape.",
        "residual_risk": "Semantic readability and Product Reality remain separate.",
    }


def test_candidate_is_content_addressed_and_non_authoritative() -> None:
    result = validate_candidate(candidate())
    assert len(result["candidate_id"]) == 20
    assert result["status"] == "CANDIDATE"
    assert result["authority"] == "NON_AUTHORITATIVE"
    assert result["product_reality_may_be_inferred"] is False


def test_candidate_cannot_mark_itself_verified_or_authoritative() -> None:
    payload = candidate()
    payload["status"] = "VERIFIED"
    with pytest.raises(LearningCandidateError, match="must remain CANDIDATE"):
        validate_candidate(payload)

    payload = candidate()
    payload["authority"] = "AUTHORITATIVE"
    with pytest.raises(LearningCandidateError, match="NON_AUTHORITATIVE"):
        validate_candidate(payload)


def test_candidate_secret_material_is_rejected() -> None:
    payload = candidate()
    payload["observation"] = "api_key=literal-value"
    with pytest.raises(LearningCandidateError, match="secret material"):
        validate_candidate(payload)


def test_store_rejects_content_identity_drift() -> None:
    normalized = validate_candidate(candidate())
    raw = candidate()
    raw["candidate_id"] = normalized["candidate_id"]
    raw["proposed_rule"] = "changed rule"
    with pytest.raises(LearningCandidateError, match="candidate_id does not match"):
        validate_store({"schema_version": 1, "entries": [raw]})


def test_promotion_requires_an_independent_verifier() -> None:
    payload = verification()
    payload["independent_verifier"] = "codex"
    with pytest.raises(LearningCandidateError, match="own independent verifier"):
        build_promotion_packet(candidate(), payload)


def test_promotion_packet_uses_existing_verified_memory_contract_without_auto_promotion() -> None:
    source = candidate()
    source_before = copy.deepcopy(source)
    packet = build_promotion_packet(source, verification())
    assert source == source_before
    assert packet["promotion_state"] == "READY_FOR_EXPLICIT_PROMOTION"
    assert packet["auto_promoted"] is False
    assert packet["candidate_confidence_is_authority"] is False
    assert packet["lesson"]["evidence_state"] == "VERIFIED"
    assert packet["lesson"]["project"] == "knowledge-engine"
    assert packet["lesson"]["repository"] == "jweter/knowledge-engine-ai"
