import pytest

from project_orchestrator.architecture_learning import (
    learn_engineering_patterns,
    retrieve_architecture_guidance,
)


def observation(pattern, outcome, ref, *, repo="jweter/knowledge-engine-core", area="worker"):
    return {
        "repository": repo,
        "area": area,
        "pattern": pattern,
        "outcome": outcome,
        "evidence_state": "VERIFIED_SANITIZED",
        "source_ref": ref,
        "verification": "exact-head preflight and fresh CI green",
        "maintenance_signal": "subsequent bounded modification did not require repair",
    }


def test_repeated_verified_success_can_become_reuse_candidate():
    learned = learn_engineering_patterns(
        [observation("platform-boundary-helper", "SUCCESS", "pr#1"), observation("platform-boundary-helper", "SUCCESS", "pr#2")],
        repository="jweter/knowledge-engine-core",
        area="worker",
    )
    assert [item["pattern"] for item in learned["trusted_patterns"]] == ["platform-boundary-helper"]


def test_merge_or_single_success_is_not_enough_to_train_good_pattern():
    learned = learn_engineering_patterns(
        [observation("large-shared-module", "SUCCESS", "pr#3")],
        repository="jweter/knowledge-engine-core",
        area="worker",
    )
    assert learned["trusted_patterns"] == []
    assert learned["uncertain_patterns"][0]["pattern"] == "large-shared-module"


def test_verified_contradiction_prevents_positive_learning():
    learned = learn_engineering_patterns(
        [
            observation("platform-boundary-helper", "SUCCESS", "pr#1"),
            observation("platform-boundary-helper", "SUCCESS", "pr#2"),
            observation("platform-boundary-helper", "FAILURE", "issue#9"),
        ],
        repository="jweter/knowledge-engine-core",
        area="worker",
    )
    assert learned["trusted_patterns"] == []
    assert learned["uncertain_patterns"][0]["failure_count"] == 1


def test_repeated_verified_failure_can_become_avoid_candidate():
    learned = learn_engineering_patterns(
        [observation("posix-call-inline", "FAILURE", "issue#1"), observation("posix-call-inline", "FAILURE", "issue#2")],
        repository="jweter/knowledge-engine-core",
        area="worker",
    )
    assert learned["avoid_patterns"][0]["pattern"] == "posix-call-inline"


def test_repository_and_area_scope_prevents_cross_pollination():
    learned = learn_engineering_patterns(
        [
            observation("fixture-shape", "SUCCESS", "a", repo="jweter/Project-Everward", area="save"),
            observation("fixture-shape", "SUCCESS", "b", repo="jweter/Project-Everward", area="save"),
        ],
        repository="jweter/rocksmith-cdlc-generator",
        area="export",
    )
    assert learned["trusted_patterns"] == []


def test_unverified_observation_fails_closed():
    item = observation("pattern", "SUCCESS", "pr#1")
    item["evidence_state"] = "UNVERIFIED"
    with pytest.raises(ValueError, match="verified"):
        learn_engineering_patterns([item], repository="jweter/knowledge-engine-core", area="worker")


def test_retrieval_does_not_infer_unknown_pattern_quality():
    learned = learn_engineering_patterns(
        [observation("helper", "SUCCESS", "a"), observation("helper", "SUCCESS", "b")],
        repository="jweter/knowledge-engine-core",
        area="worker",
    )
    guidance = retrieve_architecture_guidance(learned, candidate_patterns=["helper", "new-abstraction"])
    assert [item["pattern"] for item in guidance["reuse"]] == ["helper"]
    assert guidance["unknown"] == ["new-abstraction"]
