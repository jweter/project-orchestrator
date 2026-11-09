"""Regression: self-authored acceptance must not launder placeholders into authoritative tests."""
from __future__ import annotations

import json

import pytest

from project_orchestrator import iron_god_local_drafter as drafter


def request(expected, *, oracle="SELF_AUTHORED"):
    return {
        "id": "RM-research-t1-r1",
        "repository": "jweter/knowledge-engine-ai",
        "slice": "ai-online-report.t1",
        "route": {"builder": "LOCAL"},
        "capability_level": 1,
        "decomposition": {"parent": "RM-research", "key": "t1", "oracle": oracle},
        "acceptance_examples": [
            {"call": "{fn}('Climate change impacts')", "expected": expected, "why": "report"},
            {"call": "{fn}('Quantum Computing')", "expected": expected, "why": "report"},
        ],
    }


def test_reproduces_real_research_report_placeholder_in_json_encoded_expected():
    expected = json.dumps({
        "title": "Climate Change Impacts",
        "sections": [{"title": "Introduction", "content": "..."},
                     {"title": "Results", "content": "..."}],
    })
    model_authored = request(expected)
    assert drafter.self_authored_example_problems(model_authored) == [
        "SELF_AUTHORED_ACCEPTANCE_PLACEHOLDER: example 1 has no verified exact output",
        "SELF_AUTHORED_ACCEPTANCE_PLACEHOLDER: example 2 has no verified exact output",
    ]
    assert not drafter.eligible(model_authored)


@pytest.mark.parametrize("expected", [
    "...",
    "…",
    {"sections": [{"content": "TODO"}]},
    ["valid", {"nested": ["...", "valid"]}],
    json.dumps([{"content": "TBD"}]),
])
def test_blocks_exact_unresolved_values_for_model_authored_oracle(expected):
    assert len(drafter.self_authored_example_problems(request(expected))) == 2
    assert not drafter.eligible(request(expected))


def test_model_authored_legitimate_examples_and_punctuation_are_not_refused():
    for expected in ("Finished... yes", {"sections": [{"content": "Source text supports this"}]},
                     json.dumps({"text": "Normal data. No placeholder."}), "{}"):
        item = request(expected)
        assert drafter.self_authored_example_problems(item) == []
        assert drafter.eligible(item)


def test_explicit_owner_oracle_can_intentionally_require_literal_ellipsis():
    # Do not override owner-declared exact behavior based on superficial punctuation.
    item = request(json.dumps({"content": "..."}), oracle="OWNER_DECLARED")
    assert drafter.self_authored_example_problems(item) == []
    assert drafter.eligible(item)


def test_bad_self_authored_oracle_never_starts_a_model_or_creates_a_draft(tmp_path):
    item = request(json.dumps({"sections": [{"content": "..."}]}))
    launched = []
    outcomes = drafter.advance(
        tmp_path, [item], tmp_path, "a" * 40, spawn=lambda *args: launched.append(args) or 111,
    )
    assert launched == []
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == "NEEDS_INDEPENDENT_ACCEPTANCE_EVIDENCE"
    assert "SELF_AUTHORED_ACCEPTANCE_PLACEHOLDER" in outcomes[0]["reason"]
    assert not (tmp_path / drafter.JOBS).exists()
    assert not (tmp_path / drafter.RESULTS).exists()
    assert not (tmp_path / "drafts").exists()


def test_no_stale_harvest_may_convert_bad_self_authored_oracle_to_draft():
    item = request("...")
    with pytest.raises(drafter.ContradictsRoadmap, match="SELF_AUTHORED_ACCEPTANCE_PLACEHOLDER"):
        drafter.plan_to_draft(item, {}, {})


def test_draft_prompt_does_not_launder_model_examples_into_owner_approval():
    from scripts import iron_god_draft_author as author

    kwargs = {
        "roadmap_ref": "engineering/capability-graph.json#ai-research-report-v1",
        "objective": "Compose a real online research report from evidence",
        "allowed_paths": ["knowledge_engine_ai/generate_research_report.py"],
        "forbidden_paths": [],
        "slice_acceptance": ["Report must project actual online evidence"],
    }
    proposed = {**request("Actual evidence", oracle="SELF_AUTHORED"), **kwargs}
    prompt = author.guide_text(proposed)
    assert "proposed by a model, NOT independently approved by the owner" in prompt
    assert "owner's acceptance examples" not in prompt
    declared = {**request("Actual evidence", oracle="OWNER_DECLARED"), **kwargs}
    prompt = author.guide_text(declared)
    assert "owner's acceptance examples" in prompt
    assert "proposed by a model" not in prompt


def test_valid_self_authored_example_retains_provisional_provenance_in_draft():
    req = {
        **request("confirmed content"),
        "capability": "research-v1",
        "roadmap_ref": "engineering/capability-graph.json#research-v1/report.t1",
        "objective": "Generate report based on real evidence",
    }
    checked = {
        "function_name": "generate_report",
        "module_path": "pkg/generate_report.py",
        "signature": "def generate_report(topic: str) -> str:",
        "test_path": "tests/test_generate_report.py",
        "objective": "Return evidence-grounded results",
        "examples": [],
        "acceptance_criteria": ["must use actual evidence"],
        "note": "requires separately verified data bridge",
    }
    draft = drafter.plan_to_draft(req, {}, checked)
    assert all("self-authored decomposition example (unverified):" in e["why"]
               for e in draft["build"]["examples"])
    assert any("(self-authored decomposition, unverified)" in c
               for c in draft["acceptance_criteria"])
    assert not any("(roadmap acceptance)" in c for c in draft["acceptance_criteria"])
