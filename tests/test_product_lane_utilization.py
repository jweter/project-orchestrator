from __future__ import annotations

import json

from project_orchestrator.planning_dispatch import select_execution_target


PRODUCTS = ["knowledge-engine", "rocksmith", "everward", "voicestudio"]
ACTIVATION = {
    "products": {
        "knowledge-engine": {
            "repositories": {
                "jweter/knowledge-engine-ai": "a" * 40,
                "jweter/knowledge-engine-core": "b" * 40,
                "jweter/knowledge-engine-web": "c" * 40,
            }
        },
        "rocksmith": {
            "repositories": {
                "jweter/rocksmith-cdlc-generator": "d" * 40,
            }
        },
        "everward": {
            "repositories": {
                "jweter/Project-Everward": "e" * 40,
            }
        },
        "voicestudio": {
            "repositories": {
                "jweter/VoiceStudio": "f" * 40,
            }
        },
    }
}


def _ready(*args):
    return {"status": "READY", "allowed_paths": ["src/example.py"]}


def _unowned(*args):
    return None


def test_all_eligible_products_receive_turn_before_knowledge_engine_repeats(tmp_path):
    picks = [
        select_execution_target(
            tmp_path,
            ACTIVATION,
            PRODUCTS,
            load_execution_slice_fn=_ready,
            conflict_check_fn=_unowned,
        )
        for _ in range(5)
    ]

    picked_products = []
    for _repo, _head in picks:
        payload = json.loads((tmp_path / "dispatch-decision.json").read_text(encoding="utf-8"))
        # dispatch-decision is overwritten after each call, so derive product from repository below.
        break

    repo_to_product = {
        "jweter/knowledge-engine-ai": "knowledge-engine",
        "jweter/knowledge-engine-core": "knowledge-engine",
        "jweter/knowledge-engine-web": "knowledge-engine",
        "jweter/rocksmith-cdlc-generator": "rocksmith",
        "jweter/Project-Everward": "everward",
        "jweter/VoiceStudio": "voicestudio",
    }
    picked_products = [repo_to_product[repo] for repo, _ in picks]

    assert picked_products[:4] == PRODUCTS
    assert picked_products[4] == "knowledge-engine"


def test_knowledge_engine_subrepositories_rotate_across_product_rounds(tmp_path):
    picks = [
        select_execution_target(
            tmp_path,
            ACTIVATION,
            PRODUCTS,
            load_execution_slice_fn=_ready,
            conflict_check_fn=_unowned,
        )
        for _ in range(9)
    ]

    ke_repositories = [
        repo for repo, _ in picks
        if repo.startswith("jweter/knowledge-engine-")
    ]
    assert ke_repositories == [
        "jweter/knowledge-engine-ai",
        "jweter/knowledge-engine-core",
        "jweter/knowledge-engine-web",
    ]


def test_blocked_product_does_not_prevent_other_product_from_using_capacity(tmp_path):
    def load(_state, repo, _head):
        if repo.startswith("jweter/knowledge-engine-"):
            return {"status": "NO_WORK"}
        return {"status": "READY", "allowed_paths": ["src/example.py"]}

    repo, _ = select_execution_target(
        tmp_path,
        ACTIVATION,
        PRODUCTS,
        load_execution_slice_fn=load,
        conflict_check_fn=_unowned,
    )
    assert repo == "jweter/rocksmith-cdlc-generator"

    evidence = json.loads((tmp_path / "dispatch-decision.json").read_text(encoding="utf-8"))
    assert evidence["allocation_policy"] == "PRODUCT_FIRST_FAIR_SHARE"
    assert evidence["selected_product"] == "rocksmith"
    assert any(
        row["product"] == "knowledge-engine" and row["outcome"] == "NO_AUTHORIZED_SLICE"
        for row in evidence["decisions"]
    )


def test_owned_product_rotates_to_next_eligible_product(tmp_path):
    def conflict(_state, repo, _execution):
        if repo.startswith("jweter/knowledge-engine-"):
            return {"grant_id": "ke-owned", "state": "RUNNING"}
        return None

    repo, _ = select_execution_target(
        tmp_path,
        ACTIVATION,
        PRODUCTS,
        load_execution_slice_fn=_ready,
        conflict_check_fn=conflict,
    )

    assert repo == "jweter/rocksmith-cdlc-generator"


def test_roadmap_exhaustion_is_explicit_in_dispatch_evidence(tmp_path):
    def exhausted(_state, _repo, _head):
        return {"status": "NO_WORK", "reason": "ROADMAP_EXHAUSTED"}

    try:
        select_execution_target(
            tmp_path,
            ACTIVATION,
            PRODUCTS,
            load_execution_slice_fn=exhausted,
            conflict_check_fn=_unowned,
        )
    except RuntimeError as exc:
        assert str(exc) == "ALL_EXECUTION_LANES_COMPLETE"
    else:
        raise AssertionError("fully exhausted portfolio must terminate explicitly")

    evidence = json.loads((tmp_path / "dispatch-decision.json").read_text(encoding="utf-8"))
    assert evidence["selected_repository"] is None
    assert {row["outcome"] for row in evidence["decisions"]} == {"ROADMAP_EXHAUSTED"}
    assert {row["reason"] for row in evidence["decisions"]} == {"ROADMAP_EXHAUSTED"}
