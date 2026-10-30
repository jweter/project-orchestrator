"""Worker failure reporting preserves useful classes without exposing private exceptions."""

from __future__ import annotations

import json

import pytest

from project_orchestrator.supervisor_runtime import (
    harvest_parallel_builders,
    worker_exception_diagnostic,
)


@pytest.mark.parametrize(
    ("error", "failure_class", "failure_code"),
    [
        (
            RuntimeError(
                "This tool call was blocked by OpenAI's safety checks: token=private"
            ),
            "PLATFORM_DENIAL",
            "PLATFORM_SAFETY_DENIED",
        ),
        (
            RuntimeError("[Create Unsafe Agents]: private local path"),
            "PLATFORM_DENIAL",
            "PLATFORM_SAFETY_DENIED",
        ),
        (
            RuntimeError("OWNER_CONTROL_UNVERIFIED"),
            "AUTHORIZATION_DENIED",
            "OWNER_CONTROL_UNVERIFIED",
        ),
        (
            RuntimeError("EXECUTION_SLICE_ALREADY_OWNED:private-grant-id"),
            "OWNERSHIP_BLOCKED",
            "EXECUTION_SLICE_ALREADY_OWNED",
        ),
        (
            RuntimeError("PLANNED_SLICE_IDENTITY_MISMATCH"),
            "PRECONDITION_FAILED",
            "PLANNED_SLICE_IDENTITY_MISMATCH",
        ),
        (
            PermissionError("C:/Users/private/token.txt"),
            "ENVIRONMENT_FAILURE",
            "PermissionError",
        ),
        (
            ValueError("secret=private"),
            "CODE_FAILURE",
            "ValueError",
        ),
        (
            RuntimeError("private unrecognized failure from a model"),
            "UNCLASSIFIED",
            "UNCLASSIFIED_EXCEPTION",
        ),
    ],
)
def test_worker_exception_diagnostics_are_redacted(error, failure_class, failure_code):
    evidence = worker_exception_diagnostic(error)
    assert evidence["failure_class"] == failure_class
    assert evidence["failure_code"] == failure_code
    serialized = json.dumps(evidence)
    for forbidden in ("private", "token.txt", "C:/Users", "model"):
        assert forbidden not in serialized


class CompletedPool:
    def __init__(self, error: BaseException, worker_id: str = "everward-worker-1"):
        self.error = error
        self.worker_id = worker_id

    def harvest(self) -> dict:
        return {self.worker_id: self.error}


@pytest.mark.parametrize(
    "worker_id",
    (
        "knowledge-engine-worker-1",
        "rocksmith-worker-1",
        "everward-worker-1",
        "voicestudio-worker-1",
        "iron-god-worker-1",
    ),
)
def test_failure_classification_is_engine_wide(tmp_path, worker_id):
    rows = harvest_parallel_builders(
        CompletedPool(RuntimeError("EXECUTION_GRANT_REQUIRED"), worker_id), tmp_path
    )
    assert rows[worker_id]["failure_class"] == "AUTHORIZATION_DENIED"
    assert rows[worker_id]["failure_code"] == "EXECUTION_GRANT_REQUIRED"
    assert rows[worker_id]["status"] == "WORKER_EXCEPTION"


def test_harvest_persists_safe_failure_without_leaking_exception(tmp_path):
    error = RuntimeError(
        "This tool call was blocked by OpenAI's safety checks: key=private"
    )
    rows = harvest_parallel_builders(CompletedPool(error), tmp_path)
    saved = json.loads(
        (tmp_path / "parallel-worker-results.json").read_text(encoding="utf-8")
    )
    assert saved["completed"] == rows
    row = rows["everward-worker-1"]
    assert row["status"] == "WORKER_EXCEPTION"
    assert row["error_class"] == "RuntimeError"
    assert row["failure_class"] == "PLATFORM_DENIAL"
    assert row["failure_code"] == "PLATFORM_SAFETY_DENIED"
    assert "private" not in json.dumps(saved)


def test_only_allowlisted_source_site_is_reported():
    namespace: dict = {}
    try:
        exec(
            compile(
                "raise RuntimeError('secret=private')",
                "C:/Users/private/untrusted.py",
                "exec",
            ),
            namespace,
        )
    except RuntimeError as error:
        assert "failure_site" not in worker_exception_diagnostic(error)

    try:
        exec(
            compile(
                "raise RuntimeError('secret=private')",
                "C:/Users/private/supervisor_runtime.py",
                "exec",
            ),
            namespace,
        )
    except RuntimeError as error:
        site = worker_exception_diagnostic(error)["failure_site"]
        assert site == "supervisor_runtime.py:1"
        assert "private" not in site


def test_harvest_does_not_convert_exception_to_success(tmp_path):
    rows = harvest_parallel_builders(
        CompletedPool(RuntimeError("OWNER_CONTROL_UNVERIFIED")), tmp_path
    )
    assert rows["everward-worker-1"]["status"] == "WORKER_EXCEPTION"
    assert "healthy" not in rows["everward-worker-1"]
    assert rows["everward-worker-1"]["failure_class"] == "AUTHORIZATION_DENIED"
