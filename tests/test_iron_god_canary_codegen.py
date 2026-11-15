"""Pure codegen safety and real disposable-Git recovery with injected local responses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_orchestrator import iron_god_canary_codegen as codegen
from tests.test_iron_god_three_microedit_resume_canary import run_disposable_three_edits

SOURCES = (
    "def alpha() -> int:\n    return 1 + 1\n",
    "def beta() -> int:\n    return 10 - 5\n",
    "def gamma() -> int:\n    return 3 * 3\n",
)


@pytest.mark.parametrize("i", [0, 1, 2])
def test_accepted_local_snippet_is_one_strict_pure_function(i: int) -> None:
    source, error = codegen.validate_source(i, SOURCES[i])
    assert error is None
    assert source == SOURCES[i]
    assert codegen.TASKS[i][0] in codegen.prompt_for(i)
    assert codegen.TASKS[i][2] in codegen.prompt_for(i)


@pytest.mark.parametrize(
    "raw",
    [
        "def alpha() -> int:\n    return 2\n",
        "import os\ndef alpha() -> int:\n    return 1 + 1\n",
        "def alpha() -> int:\n    return __import__('os').system('echo bad')\n",
        "def alpha() -> int:\n    return open('secrets')\n",
        "def alpha() -> int:\n    return 1 / 1\n",
        "def alpha() -> int:\n    return 1 // 0\n",
        "def alpha(x: int) -> int:\n    return x + 1\n",
        "@property\ndef alpha() -> int:\n    return 1 + 1\n",
        "def beta() -> int:\n    return 1 + 1\n",
        "def alpha() -> int:\n    global x\n    return 1+1\n",
        "def alpha() -> int:\n    return (1 + 1).__class__\n",
        "def alpha() -> int:\n    return 1 + True\n",
    ],
)
def test_rejects_unsafe_unscoped_or_trivial_source(raw: str) -> None:
    assert codegen.validate_source(0, raw)[0] is None


def test_two_bounded_proposals_with_local_feedback() -> None:
    calls: list[str] = []
    inputs = iter(
        [{"source": "def alpha() -> int:\n    return 2\n"}, {"source": SOURCES[0]}]
    )

    def model(prompt: str):
        calls.append(prompt)
        return next(inputs)

    result = codegen.propose_source(0, model, lambda: True)
    assert result["status"] == "LOCAL_SOURCE_VALIDATED"
    assert result["attempted_proposals"] == 2
    assert len(calls) == 2
    assert "UNSAFE_OR_TRIVIAL_RETURN_EXPRESSION" in calls[-1]
    assert result["source"] == SOURCES[0]
    assert len(result["sha256"]) == 64


def test_model_timeout_is_wait_not_exhausted_source() -> None:
    def unavailable(_prompt):
        raise TimeoutError("OLLAMA memory admission wait")

    result = codegen.propose_source(0, unavailable, lambda: True)
    assert result["status"] == "LOCAL_MODEL_UNAVAILABLE"
    assert result["error_type"] == "TimeoutError"


def test_malformed_json_consumes_only_bounded_proposals() -> None:
    calls = []

    def malformed(prompt: str):
        calls.append(prompt)
        raise json.JSONDecodeError("truncated", "{", 1)

    result = codegen.propose_source(0, malformed, lambda: True)
    assert result["status"] == "LOCAL_CODEGEN_REJECTED"
    assert len(calls) == codegen.MAX_PROPOSALS
    assert result["validation_failures"] == ["INVALID_SOURCE_SIZE"] * 2


def test_owner_pause_during_model_prevents_publication() -> None:
    permitted = [True]

    def model(_prompt):
        permitted[0] = False
        return {"source": SOURCES[0]}

    assert codegen.propose_source(0, model, lambda: permitted[0]) == {
        "status": "OWNER_AUTHORITY_DENIED_AFTER_MODEL"
    }
    assert codegen.propose_source(
        0, lambda _: pytest.fail("should not call"), lambda: False
    ) == {"status": "OWNER_AUTHORITY_DENIED"}


def test_three_injected_locally_validated_changes_use_real_git_and_sqlite(
    tmp_path: Path,
) -> None:
    model_calls: list[int] = []

    def builder(index: int) -> str:
        model_calls.append(index)
        result = codegen.propose_source(
            index, lambda _prompt: {"source": SOURCES[index]}, lambda: True
        )
        assert result["status"] == "LOCAL_SOURCE_VALIDATED"
        return result["source"]

    result = run_disposable_three_edits(tmp_path, source_builder=builder)
    assert model_calls == [0, 1, 2]
    assert len(set(result["verified_commits"])) == 3
    assert result["native_tests_green_after_each"]
    assert result["resumed_without_duplicate"]
    assert result["disposable_only"]
