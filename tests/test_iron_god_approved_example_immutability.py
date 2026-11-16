"""The local retry strategy must never rewrite the owner's approved examples."""
from __future__ import annotations

import copy
import inspect

from scripts import iron_god_product_worker as worker


OWNER_CASE = "claim_coverage_ratio(['Insufficient_Evidence'])"


def plan():
    return {
        "function_name": "claim_coverage_ratio",
        "examples": [
            {"call": OWNER_CASE, "expected": "1.0"},
            {"call": "claim_coverage_ratio([])", "expected": "0.0"},
            {"call": "claim_coverage_ratio(['insufficient_evidence'])", "expected": "0.0"},
        ],
        "acceptance_criteria": [
            OWNER_CASE + " returns 1.0",
            "claim_coverage_ratio([]) returns 0.0",
            "claim_coverage_ratio(['insufficient_evidence']) returns 0.0",
        ],
    }


def test_approved_examples_are_immutable(tmp_path, monkeypatch):
    spec = plan()
    original = copy.deepcopy(spec)
    packet = {"slice": {"build": {"function_name": "claim_coverage_ratio",
                                   "examples": [{"call": OWNER_CASE, "expected": 1.0}]}}}
    monkeypatch.setattr(worker, "actual_result",
                        lambda *_: "0.0")

    def wrong_fresh_model(*_args):
        raise AssertionError("no model rederivation is permitted for approved tests")

    def no_execution():
        raise AssertionError("approved test adjudication must not run arbitrary code")

    corrected = worker.adjudicate(
        tmp_path, packet, spec,
        "FAILED tests/test_online_evidence_claim_coverage.py::test_claim_coverage_ratio_example_1",
        no_execution, wrong_fresh_model,
    )
    assert corrected == []
    assert spec == original
    assert "returns 1.0" in worker.render_tests(spec).replace(" == ", " returns ") or (
        "assert " + OWNER_CASE + " == 1.0" in worker.render_tests(spec)
    )


def test_approved_empty_and_malformed_packet_also_do_not_modify_examples(tmp_path):
    spec = plan()
    original = copy.deepcopy(spec)
    for packet in (
        {"slice": {"build": {"examples": []}}},
        {"slice": {"build": {"function_name": "claim_coverage_ratio"}}},
    ):
        assert worker.adjudicate(
            tmp_path, packet, spec,
            "FAILED test_claim_coverage_ratio_example_1",
            lambda: None, lambda *_: 0.0,
        ) == []
        assert spec == original


def test_nonapproved_provisional_examples_retains_prior_adjudication(tmp_path, monkeypatch):
    spec = plan()
    monkeypatch.setattr(worker, "actual_result", lambda *_: "0.0")
    corrected = worker.adjudicate(
        tmp_path, {"slice": {"id": "model-proposal"}}, spec,
        "FAILED test_claim_coverage_ratio_example_1",
        lambda: None, lambda *_: 0.0,
    )
    assert corrected == [{"example": 1, "call": OWNER_CASE, "was": "1.0", "now": "0.0"}]
    assert spec["examples"][0]["expected"] == "0.0"


def test_execution_path_never_invokes_adjudication_for_approved_backlog():
    source = inspect.getsource(worker.execute)
    assert 'and not approved:' in source
    assert 'corrected = adjudicate(' in source
