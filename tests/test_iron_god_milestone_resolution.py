from __future__ import annotations

import pytest

from project_orchestrator.iron_god_milestone_resolution import (
    MilestoneResolutionError,
    resolve_latest_committed_milestone,
)
from project_orchestrator.local_first_state import LocalFirstState

REPO = "demo-owner/demo-repo"
OTHER_REPO = "demo-owner/other-repo"


def _row(
    milestone_id: str,
    *,
    repository: str = REPO,
    state: str = "COMMITTED",
    accepted_sha: str = "a" * 40,
    changed_at: str = "2026-10-17T00:00:00Z",
) -> dict:
    return {
        "milestone_id": milestone_id,
        "repository": repository,
        "state": state,
        "accepted_sha": accepted_sha,
        "changed_at": changed_at,
    }


def _snapshot(rows: list[dict]) -> dict:
    return {"acceptance_intents": rows}


def test_no_rows_for_repository_returns_none():
    assert resolve_latest_committed_milestone(_snapshot([]), REPO) is None


def test_prepared_not_committed_returns_none():
    rows = [_row("M-1", state="PREPARED")]
    assert resolve_latest_committed_milestone(_snapshot(rows), REPO) is None


def test_single_committed_row_is_returned():
    rows = [_row("M-1", accepted_sha="b" * 40, changed_at="2026-10-17T01:00:00Z")]
    result = resolve_latest_committed_milestone(_snapshot(rows), REPO)
    assert result.milestone_id == "M-1"
    assert result.repository == REPO
    assert result.accepted_sha == "b" * 40
    assert result.changed_at == "2026-10-17T01:00:00Z"


def test_multiple_committed_rows_returns_latest_by_changed_at():
    rows = [
        _row("M-1", changed_at="2026-10-15T00:00:00Z"),
        _row("M-2", changed_at="2026-10-17T00:00:00Z"),
        _row("M-3", changed_at="2026-10-16T00:00:00Z"),
    ]
    result = resolve_latest_committed_milestone(_snapshot(rows), REPO)
    assert result.milestone_id == "M-2"


def test_row_for_different_repository_is_ignored():
    rows = [_row("M-1", repository=OTHER_REPO)]
    assert resolve_latest_committed_milestone(_snapshot(rows), REPO) is None


def test_duplicate_milestone_id_raises():
    rows = [_row("M-1"), _row("M-1")]
    with pytest.raises(MilestoneResolutionError):
        resolve_latest_committed_milestone(_snapshot(rows), REPO)


def test_missing_acceptance_intents_key_raises():
    with pytest.raises(MilestoneResolutionError):
        resolve_latest_committed_milestone({}, REPO)


def test_malformed_row_raises():
    rows = [{"repository": REPO, "state": "COMMITTED"}]  # no milestone_id
    with pytest.raises(MilestoneResolutionError):
        resolve_latest_committed_milestone(_snapshot(rows), REPO)


def test_real_local_first_state_end_to_end(tmp_path):
    state = LocalFirstState(tmp_path / "accepted.sqlite")

    state.prepare_acceptance(
        repository=REPO,
        milestone_id="M-first",
        accepted_base_sha="0" * 40,
        accepted_sha="1" * 40,
        verification_sha256="a" * 64,
        product_reality="NOT_REQUIRED",
        context_json="{}",
    )
    state.finish_prepared_acceptance(
        milestone_id="M-first",
        expected_sha="1" * 40,
        expected_verification_sha256="a" * 64,
    )

    result = resolve_latest_committed_milestone(state.snapshot(), REPO)
    assert result.milestone_id == "M-first"
    assert result.accepted_sha == "1" * 40

    state.prepare_acceptance(
        repository=REPO,
        milestone_id="M-second",
        accepted_base_sha="1" * 40,
        accepted_sha="2" * 40,
        verification_sha256="b" * 64,
        product_reality="NOT_REQUIRED",
        context_json="{}",
    )
    state.finish_prepared_acceptance(
        milestone_id="M-second",
        expected_sha="2" * 40,
        expected_verification_sha256="b" * 64,
    )

    result = resolve_latest_committed_milestone(state.snapshot(), REPO)
    assert result.milestone_id == "M-second"
    assert result.accepted_sha == "2" * 40


def test_real_local_first_state_no_committed_milestone_yet_returns_none(tmp_path):
    state = LocalFirstState(tmp_path / "accepted.sqlite")
    assert resolve_latest_committed_milestone(state.snapshot(), REPO) is None

    # A merely-prepared (not finished) acceptance must not count either.
    state.prepare_acceptance(
        repository=REPO,
        milestone_id="M-pending",
        accepted_base_sha="0" * 40,
        accepted_sha="1" * 40,
        verification_sha256="a" * 64,
        product_reality="NOT_REQUIRED",
        context_json="{}",
    )
    assert resolve_latest_committed_milestone(state.snapshot(), REPO) is None
