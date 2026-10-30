"""Isolated contract tests; synthetic runner results are NOT Product Reality."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from project_orchestrator.iron_god_knowledge_engine_adapter import (
    EXPECTED_SOURCE_CONTEXT_USE,
    GRACEFUL_FAILURE_ON_MISSING_INFO,
    LATENCY_WITHIN_BOUNDS,
    NO_HALLUCINATED_BEHAVIOR,
    RETRIEVAL_FUNCTIONING,
    USABLE_ANSWERS,
)
from project_orchestrator.iron_god_knowledge_engine_live_retrieval import (
    GoldenRetrievalCase,
    KnowledgeEngineLiveRetrievalAdapter,
)
from project_orchestrator.iron_god_product_reality_adapter import FAIL, HUMAN_DEBT, PASS

_SHA = "a" * 40
_RECORD = "ev-glp1-step5-body-weight-week104-001"
_QUESTION = "Does semaglutide reduce body weight in adults with obesity?"
_NEGATIVE = "zzqneverexistsxyy999"


def _adapter(tmp_path: Path, runner, **overrides):
    for name in ("python.exe", "ke", "sources.csv", "evidence.jsonl"):
        (tmp_path / name).write_text("test", encoding="utf-8")
    kwargs = dict(
        expected_repository="knowledge-engine-core",
        expected_candidate_sha=_SHA,
        core_root=tmp_path,
        python_executable=tmp_path / "python.exe",
        ke_script=tmp_path / "ke",
        sources=tmp_path / "sources.csv",
        evidence=tmp_path / "evidence.jsonl",
        cases=(GoldenRetrievalCase(_QUESTION, (_RECORD,)),),
        negative_question=_NEGATIVE,
        runner=runner,
    )
    kwargs.update(overrides)
    return KnowledgeEngineLiveRetrievalAdapter(**kwargs)


def _payload(question: str, *, found=True, cited=True):
    papers = []
    if question != _NEGATIVE:
        papers = [
            {
                "citation": "Example citation" if cited else "",
                "doi": "10.1038/example" if cited else "",
                "source_url": "",
                "evidence_records": [
                    {"evidence_record_id": _RECORD if found else "different-id"}
                ],
            }
        ]
    return {"schema_version": 1, "question": question, "papers": papers}


def _runner(command, **kwargs):
    assert kwargs["shell"] is False
    assert kwargs["check"] is False
    assert kwargs["timeout"] <= 120
    assert command[2] == "evidence-report"
    assert command[-2:] == ["--format", "json"]
    return SimpleNamespace(returncode=0, stdout=json.dumps(_payload(command[3])))


def _statuses(evidence):
    return {item.category: item.status for item in evidence.category_results}


def test_real_cli_command_contract_and_semantic_debt(tmp_path):
    adapter = _adapter(tmp_path, _runner)
    result = adapter.evaluate("knowledge-engine-core", _SHA)
    statuses = _statuses(result)
    assert statuses[RETRIEVAL_FUNCTIONING] == PASS
    assert statuses[EXPECTED_SOURCE_CONTEXT_USE] == PASS
    assert statuses[LATENCY_WITHIN_BOUNDS] == PASS
    assert statuses[GRACEFUL_FAILURE_ON_MISSING_INFO] == PASS
    assert statuses[USABLE_ANSWERS] == HUMAN_DEBT
    assert statuses[NO_HALLUCINATED_BEHAVIOR] == HUMAN_DEBT
    assert result.overall_status == HUMAN_DEBT


def test_wrong_candidate_never_launches_cli(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("must not invoke CLI for wrong SHA")

    adapter = _adapter(tmp_path, forbidden)
    result = adapter.evaluate("knowledge-engine-core", "b" * 40)
    assert _statuses(result)[RETRIEVAL_FUNCTIONING] == FAIL


def test_missing_expected_evidence_fails_closed(tmp_path):
    def runner(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_payload(command[3], found=False)),
        )

    result = _adapter(tmp_path, runner).evaluate("knowledge-engine-core", _SHA)
    assert _statuses(result)[RETRIEVAL_FUNCTIONING] == FAIL
    assert result.overall_status == FAIL


def test_missing_citation_fails_source_context(tmp_path):
    def runner(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_payload(command[3], cited=False)),
        )

    result = _adapter(tmp_path, runner).evaluate("knowledge-engine-core", _SHA)
    assert _statuses(result)[EXPECTED_SOURCE_CONTEXT_USE] == FAIL


def test_malformed_citation_metadata_never_passes_provenance(tmp_path):
    malformed = (
        {"citation": ["not a string"]},
        {"citation": {"text": "not a string"}},
        {"citation": "   "},
        {"doi": 123, "source_url": ""},
        {"doi": "", "source_url": "  "},
    )
    for fields in malformed:

        def runner(command, **kwargs):
            payload = _payload(command[3])
            if payload["papers"]:
                payload["papers"][0].update(fields)
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

        statuses = _statuses(
            _adapter(tmp_path, runner).evaluate("knowledge-engine-core", _SHA)
        )
        assert statuses[RETRIEVAL_FUNCTIONING] == PASS
        assert statuses[EXPECTED_SOURCE_CONTEXT_USE] == FAIL


def test_source_url_only_provenance_is_valid(tmp_path):
    def runner(command, **kwargs):
        payload = _payload(command[3])
        if payload["papers"]:
            payload["papers"][0].update(doi="", source_url="https://example.org/paper")
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    statuses = _statuses(
        _adapter(tmp_path, runner).evaluate("knowledge-engine-core", _SHA)
    )
    assert statuses[EXPECTED_SOURCE_CONTEXT_USE] == PASS


def test_invalid_schema_and_nonzero_exit_fail_closed(tmp_path):
    for runner in (
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout='{"schema_version":99}'),
        lambda *a, **kw: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({**_payload(_QUESTION), "schema_version": True}),
        ),
        lambda *a, **kw: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({**_payload(_QUESTION), "schema_version": 1.0}),
        ),
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout=""),
    ):
        result = _adapter(tmp_path, runner).evaluate("knowledge-engine-core", _SHA)
        assert _statuses(result)[RETRIEVAL_FUNCTIONING] == FAIL
        assert _statuses(result)[LATENCY_WITHIN_BOUNDS] == FAIL


def test_timeout_is_not_misreported_as_engineering_success(tmp_path):
    def runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["ke"], timeout=1)

    result = _adapter(tmp_path, runner).evaluate("knowledge-engine-core", _SHA)
    assert result.overall_status == FAIL


def test_negative_query_with_matches_is_not_graceful_missing_info(tmp_path):
    def runner(command, **kwargs):
        question = command[3]
        payload = _payload(question)
        if question == _NEGATIVE:
            payload["papers"] = _payload(_QUESTION)["papers"]
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    result = _adapter(tmp_path, runner).evaluate("knowledge-engine-core", _SHA)
    assert _statuses(result)[GRACEFUL_FAILURE_ON_MISSING_INFO] == FAIL


def test_unbounded_case_count_fails_before_any_cli_launch(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid probe must never launch Core CLI")

    cases = (GoldenRetrievalCase(_QUESTION, (_RECORD,)),) * 17
    result = _adapter(tmp_path, forbidden, cases=cases).evaluate(
        "knowledge-engine-core", _SHA
    )
    assert _statuses(result)[RETRIEVAL_FUNCTIONING] == FAIL


def test_invalid_record_ids_and_questions_fail_before_cli_launch(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid probe must never launch Core CLI")

    invalid_cases = (
        (GoldenRetrievalCase(_QUESTION, (" ",)),),
        (GoldenRetrievalCase("x" * 2049, (_RECORD,)),),
        (GoldenRetrievalCase(_QUESTION, (_RECORD,) * 33),),
        (GoldenRetrievalCase(_QUESTION, (None,)),),
        ("not-a-golden-case",),
    )
    for cases in invalid_cases:
        result = _adapter(tmp_path, forbidden, cases=cases).evaluate(
            "knowledge-engine-core", _SHA
        )
        assert _statuses(result)[RETRIEVAL_FUNCTIONING] == FAIL


def test_invalid_negative_question_fails_before_cli_launch(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid probe must never launch Core CLI")

    for negative_question in (" ", "x" * 2049, 42):
        result = _adapter(
            tmp_path, forbidden, negative_question=negative_question
        ).evaluate("knowledge-engine-core", _SHA)
        assert _statuses(result)[RETRIEVAL_FUNCTIONING] == FAIL
