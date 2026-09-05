from __future__ import annotations

import pytest

from project_orchestrator.evidence_collector import (
    ORCHESTRATOR_REPOSITORY,
    EvidenceCollectorError,
    analyze_merged_pr,
    build_dispatch_evidence_window,
    build_evidence_window,
    collect_dispatch_window,
    collect_portfolio_windows,
    collect_repository_window,
    fetch_dispatch_events,
    fetch_recently_merged_pull_requests,
)
from project_orchestrator.timeseries import validate_window


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_json(self, path):
        self.calls.append(path)
        value = self.responses.get(path)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise AssertionError(f"unexpected GitHub request: {path}")
        return value


REPO = "jweter/knowledge-engine-core"
ROOT = f"/repos/{REPO}"


def merged_pr(number, *, created_at, merged_at, updated_at=None):
    return {
        "number": number,
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "created_at": created_at,
        "merged_at": merged_at,
        "updated_at": updated_at or merged_at,
        "merge_commit_sha": f"merge-sha-{number}",
    }


def check_run(name, conclusion, completed_at, status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion, "completed_at": completed_at}


def base_responses(prs, commits_by_pr=None, checks_by_sha=None):
    commits_by_pr = commits_by_pr or {}
    checks_by_sha = checks_by_sha or {}
    responses = {
        f"{ROOT}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1": prs,
    }
    for pr in prs:
        number = pr["number"]
        shas = commits_by_pr.get(number, [pr["merge_commit_sha"]])
        responses[f"{ROOT}/pulls/{number}/commits?per_page=100&page=1"] = [{"sha": s} for s in shas]
        for sha in {*shas, pr["merge_commit_sha"]}:
            responses[f"{ROOT}/commits/{sha}/check-runs?per_page=100&page=1"] = {
                "check_runs": checks_by_sha.get(sha, [])
            }
    return responses


# --- fetch_recently_merged_pull_requests -----------------------------


def test_fetch_recently_merged_filters_by_cutoff() -> None:
    from datetime import datetime, timezone

    old_pr = merged_pr(1, created_at="2026-01-01T00:00:00Z", merged_at="2026-01-02T00:00:00Z")
    new_pr = merged_pr(2, created_at="2026-09-01T00:00:00Z", merged_at="2026-09-02T00:00:00Z")
    transport = FakeTransport({
        f"{ROOT}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1": [new_pr, old_pr],
    })
    cutoff = datetime(2026, 6, 1, tzinfo=timezone.utc)
    result = fetch_recently_merged_pull_requests(transport, REPO, cutoff)
    assert [pr["number"] for pr in result] == [2]


def test_fetch_recently_merged_excludes_unmerged_closed_prs() -> None:
    from datetime import datetime, timezone

    closed_unmerged = {**merged_pr(3, created_at="2026-09-01T00:00:00Z", merged_at=None), "merged_at": None}
    transport = FakeTransport({
        f"{ROOT}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1": [closed_unmerged],
    })
    result = fetch_recently_merged_pull_requests(transport, REPO, datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert result == []


# --- analyze_merged_pr -------------------------------------------------


def test_analyze_pr_with_clean_green_run() -> None:
    pr = merged_pr(10, created_at="2026-09-05T00:00:00Z", merged_at="2026-09-05T01:00:00Z")
    checks = {"merge-sha-10": [check_run("test", "success", "2026-09-05T00:10:00Z")]}
    transport = FakeTransport(base_responses([pr], checks_by_sha=checks))
    analysis = analyze_merged_pr(transport, REPO, pr)
    assert analysis["pr_creation_to_green_seconds"] == 600
    assert analysis["merge_cycle_seconds"] == 3600
    assert analysis["ci_failure_count"] == 0
    assert analysis["ci_repeat_failure_count"] == 0
    assert analysis["failure_to_repair_seconds"] is None
    assert analysis["has_ci_evidence"] is True


def test_analyze_pr_with_failure_then_repair() -> None:
    pr = merged_pr(11, created_at="2026-09-05T00:00:00Z", merged_at="2026-09-05T02:00:00Z")
    commits_by_pr = {11: ["commit-a", "commit-b"]}
    checks_by_sha = {
        "commit-a": [check_run("test", "failure", "2026-09-05T00:10:00Z")],
        "commit-b": [check_run("test", "success", "2026-09-05T00:40:00Z")],
        "merge-sha-11": [check_run("test", "success", "2026-09-05T00:40:00Z")],
    }
    transport = FakeTransport(base_responses([pr], commits_by_pr, checks_by_sha))
    analysis = analyze_merged_pr(transport, REPO, pr)
    assert analysis["ci_failure_count"] == 1
    assert analysis["ci_repeat_failure_count"] == 0
    assert analysis["failure_to_repair_seconds"] == 1800
    assert analysis["pr_creation_to_green_seconds"] == 2400


def test_analyze_pr_repeat_failures_of_same_check_are_counted() -> None:
    pr = merged_pr(12, created_at="2026-09-05T00:00:00Z", merged_at="2026-09-05T03:00:00Z")
    commits_by_pr = {12: ["c1", "c2", "c3"]}
    checks_by_sha = {
        "c1": [check_run("lint", "failure", "2026-09-05T00:05:00Z")],
        "c2": [check_run("lint", "failure", "2026-09-05T00:10:00Z")],
        "c3": [check_run("lint", "success", "2026-09-05T00:15:00Z")],
        "merge-sha-12": [check_run("lint", "success", "2026-09-05T00:15:00Z")],
    }
    transport = FakeTransport(base_responses([pr], commits_by_pr, checks_by_sha))
    analysis = analyze_merged_pr(transport, REPO, pr)
    assert analysis["ci_failure_count"] == 2
    # same-named check failing twice: first is not a repeat, second is
    assert analysis["ci_repeat_failure_count"] == 1


def test_analyze_pr_with_no_ci_configured() -> None:
    pr = merged_pr(13, created_at="2026-09-05T00:00:00Z", merged_at="2026-09-05T00:30:00Z")
    transport = FakeTransport(base_responses([pr], checks_by_sha={}))
    analysis = analyze_merged_pr(transport, REPO, pr)
    assert analysis["pr_creation_to_green_seconds"] is None
    assert analysis["ci_failure_count"] == 0
    assert analysis["has_ci_evidence"] is False
    # merge cycle time is always knowable regardless of CI
    assert analysis["merge_cycle_seconds"] == 1800


def test_analyze_pr_merged_despite_failing_head_never_fabricates_green() -> None:
    pr = merged_pr(14, created_at="2026-09-05T00:00:00Z", merged_at="2026-09-05T01:00:00Z")
    checks = {"merge-sha-14": [check_run("test", "failure", "2026-09-05T00:50:00Z")]}
    transport = FakeTransport(base_responses([pr], checks_by_sha=checks))
    analysis = analyze_merged_pr(transport, REPO, pr)
    # head never went green (admin override merge) -> no fabricated green timestamp
    assert analysis["pr_creation_to_green_seconds"] is None


# --- build_evidence_window ----------------------------------------------


def test_build_window_covers_every_declared_metric_key() -> None:
    from project_orchestrator.timeseries import ALLOWED_METRIC_KEYS

    analyses = [{
        "number": 1, "url": "https://example.invalid/1",
        "merge_cycle_seconds": 100.0, "pr_creation_to_green_seconds": 50.0,
        "ci_failure_count": 0, "ci_repeat_failure_count": 0,
        "failure_to_repair_seconds": None, "has_ci_evidence": True,
    }]
    from datetime import datetime, timezone
    window = build_evidence_window(
        "knowledge-engine", REPO, analyses,
        window_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert set(window["metrics"]) == set(ALLOWED_METRIC_KEYS)
    validate_window(window)  # must satisfy the store's own schema


def test_build_window_leaves_unmeasurable_metrics_unknown() -> None:
    from datetime import datetime, timezone

    analyses = [{
        "number": 1, "url": "https://example.invalid/1",
        "merge_cycle_seconds": 100.0, "pr_creation_to_green_seconds": None,
        "ci_failure_count": 0, "ci_repeat_failure_count": 0,
        "failure_to_repair_seconds": None, "has_ci_evidence": False,
    }]
    window = build_evidence_window(
        "knowledge-engine", REPO, analyses,
        window_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    for key in (
        "blocked_waiting_seconds", "preflight_failures_prevented_count",
        "product_reality_debt_created_count", "product_reality_debt_resolved_count",
        "cross_repo_incident_count", "autonomous_intervention_count",
        "jeremy_required_intervention_count",
    ):
        assert window["metrics"][key]["status"] == "UNKNOWN"


def test_build_window_only_credits_roadmap_slices_to_orchestrator_repo() -> None:
    from datetime import datetime, timezone

    analyses = [{
        "number": 1, "url": "https://example.invalid/1",
        "merge_cycle_seconds": 100.0, "pr_creation_to_green_seconds": None,
        "ci_failure_count": 0, "ci_repeat_failure_count": 0,
        "failure_to_repair_seconds": None, "has_ci_evidence": False,
    }]
    product_window = build_evidence_window(
        "knowledge-engine", REPO, analyses,
        window_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    orchestrator_window = build_evidence_window(
        "portfolio", "jweter/project-orchestrator", analyses,
        window_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert product_window["metrics"]["roadmap_slices_completed_count"]["status"] == "UNKNOWN"
    assert orchestrator_window["metrics"]["roadmap_slices_completed_count"]["status"] == "MEASURED"
    assert orchestrator_window["metrics"]["roadmap_slices_completed_count"]["value"] == 1


# --- collect_repository_window / collect_portfolio_windows --------------


def test_collect_repository_window_returns_none_when_nothing_merged() -> None:
    transport = FakeTransport({
        f"{ROOT}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1": [],
    })
    store = {"schema_version": 1, "windows": []}
    result = collect_repository_window(transport, store, REPO, since="2026-01-01T00:00:00Z")
    assert result is None


def test_collect_repository_window_is_deterministic_on_replay() -> None:
    pr = merged_pr(20, created_at="2026-09-05T00:00:00Z", merged_at="2026-09-05T01:00:00Z")
    checks = {"merge-sha-20": [check_run("test", "success", "2026-09-05T00:10:00Z")]}
    transport = FakeTransport(base_responses([pr], checks_by_sha=checks))
    store = {"schema_version": 1, "windows": []}
    first = collect_repository_window(transport, store, REPO, since="2026-01-01T00:00:00Z")
    second = collect_repository_window(transport, store, REPO, since="2026-01-01T00:00:00Z")
    assert validate_window(first)["window_id"] == validate_window(second)["window_id"]


def test_collect_repository_window_rejects_unknown_repository() -> None:
    transport = FakeTransport({})
    store = {"schema_version": 1, "windows": []}
    with pytest.raises(EvidenceCollectorError, match="not a known portfolio repository"):
        collect_repository_window(transport, store, "jweter/not-tracked", since="2026-01-01T00:00:00Z")


def test_collect_repository_window_uses_prior_window_end_as_default_since() -> None:
    from project_orchestrator.timeseries import append_window

    prior = {
        "project": "knowledge-engine",
        "repository": REPO,
        "window_start": "2026-08-01T00:00:00Z",
        "window_end": "2026-08-15T00:00:00Z",
        "notes": "automatically collected from 1 merged pull request(s): #1",
        "metrics": {
            key: {"status": "UNKNOWN"} for key in __import__(
                "project_orchestrator.timeseries", fromlist=["ALLOWED_METRIC_KEYS"]
            ).ALLOWED_METRIC_KEYS
        },
    }
    store = append_window({"schema_version": 1, "windows": []}, prior)

    old_pr = merged_pr(21, created_at="2026-08-10T00:00:00Z", merged_at="2026-08-12T00:00:00Z")
    new_pr = merged_pr(22, created_at="2026-08-20T00:00:00Z", merged_at="2026-08-21T00:00:00Z")
    transport = FakeTransport(base_responses([new_pr, old_pr]))
    window = collect_repository_window(transport, store, REPO)
    assert window is not None
    assert "#22" in window["notes"]
    assert "#21" not in window["notes"]


def test_collect_portfolio_windows_rejects_unknown_repository_list() -> None:
    transport = FakeTransport({})
    store = {"schema_version": 1, "windows": []}
    with pytest.raises(EvidenceCollectorError, match="unknown repositories"):
        collect_portfolio_windows(transport, store, repositories=["jweter/not-tracked"])


def test_collect_portfolio_windows_skips_repositories_with_nothing_new() -> None:
    responses = {}
    for repo in ["jweter/knowledge-engine-core", "jweter/knowledge-engine-web"]:
        root = f"/repos/{repo}"
        responses[f"{root}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1"] = []
    transport = FakeTransport(responses)
    store = {"schema_version": 1, "windows": []}
    windows = collect_portfolio_windows(
        transport, store,
        repositories=["jweter/knowledge-engine-core", "jweter/knowledge-engine-web"],
        since="2026-01-01T00:00:00Z",
        include_dispatch_evidence=False,
    )
    assert windows == []


# --- dispatch-artifact evidence (orchestrator-observe.yml) ---------------


class FakeArtifactDownloader:
    def __init__(self, archives):
        self.archives = archives

    def get_bytes(self, url):
        value = self.archives.get(url)
        if value is None:
            raise AssertionError(f"unexpected artifact download: {url}")
        return value


def _zip_with_json(member_name, payload):
    import io as _io
    import json as _json
    import zipfile as _zipfile

    buffer = _io.BytesIO()
    with _zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member_name, _json.dumps(payload))
    return buffer.getvalue()


ORCH_ROOT = f"/repos/{ORCHESTRATOR_REPOSITORY}"


def workflow_run(run_id, created_at):
    return {"id": run_id, "created_at": created_at, "html_url": f"https://github.com/{ORCHESTRATOR_REPOSITORY}/actions/runs/{run_id}"}


def dispatch_event(state, repository="jweter/knowledge-engine-core", received_at="2026-09-05T21:29:35Z"):
    return {
        "schema_version": 1,
        "received_at": received_at,
        "source": "n8n",
        "state": state,
        "reason": "knowledge_engine_main_change" if state == "DEPENDENCY_RECHECK_CANDIDATE" else "ci_failure",
        "repository": repository,
        "pr_number": None,
        "head_sha": "a" * 40,
        "conclusion": None,
        "check_suite_id": None,
        "authority": "evidence_only_not_merge_authority",
    }


def test_fetch_dispatch_events_downloads_and_parses_artifact() -> None:
    from datetime import datetime, timezone

    run = workflow_run(1, "2026-09-05T21:29:35Z")
    archive_url = "https://example.invalid/artifact.zip"
    event = dispatch_event("DEPENDENCY_RECHECK_CANDIDATE")
    transport = FakeTransport({
        f"{ORCH_ROOT}/actions/workflows/orchestrator-observe.yml/runs?event=workflow_dispatch&status=success&per_page=100": {
            "workflow_runs": [run]
        },
        f"{ORCH_ROOT}/actions/runs/1/artifacts": {
            "artifacts": [{"name": "n8n-event", "expired": False, "archive_download_url": archive_url}]
        },
    })
    downloader = FakeArtifactDownloader({archive_url: _zip_with_json("n8n-event.json", event)})
    events = fetch_dispatch_events(transport, downloader, datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert len(events) == 1
    assert events[0]["state"] == "DEPENDENCY_RECHECK_CANDIDATE"
    assert events[0]["_run_url"] == run["html_url"]


def test_fetch_dispatch_events_skips_expired_artifact() -> None:
    from datetime import datetime, timezone

    run = workflow_run(2, "2026-09-05T21:29:35Z")
    transport = FakeTransport({
        f"{ORCH_ROOT}/actions/workflows/orchestrator-observe.yml/runs?event=workflow_dispatch&status=success&per_page=100": {
            "workflow_runs": [run]
        },
        f"{ORCH_ROOT}/actions/runs/2/artifacts": {
            "artifacts": [{"name": "n8n-event", "expired": True, "archive_download_url": "https://example.invalid/x.zip"}]
        },
    })
    events = fetch_dispatch_events(transport, FakeArtifactDownloader({}), datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert events == []


def test_fetch_dispatch_events_skips_unparseable_archive_without_raising() -> None:
    from datetime import datetime, timezone

    run = workflow_run(3, "2026-09-05T21:29:35Z")
    archive_url = "https://example.invalid/bad.zip"
    transport = FakeTransport({
        f"{ORCH_ROOT}/actions/workflows/orchestrator-observe.yml/runs?event=workflow_dispatch&status=success&per_page=100": {
            "workflow_runs": [run]
        },
        f"{ORCH_ROOT}/actions/runs/3/artifacts": {
            "artifacts": [{"name": "n8n-event", "expired": False, "archive_download_url": archive_url}]
        },
    })
    downloader = FakeArtifactDownloader({archive_url: b"not a zip file"})
    events = fetch_dispatch_events(transport, downloader, datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert events == []


def test_fetch_dispatch_events_filters_by_cutoff() -> None:
    from datetime import datetime, timezone

    old_run = workflow_run(4, "2026-01-01T00:00:00Z")
    transport = FakeTransport({
        f"{ORCH_ROOT}/actions/workflows/orchestrator-observe.yml/runs?event=workflow_dispatch&status=success&per_page=100": {
            "workflow_runs": [old_run]
        },
    })
    events = fetch_dispatch_events(transport, FakeArtifactDownloader({}), datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert events == []


def test_build_dispatch_window_counts_only_dependency_recheck_events() -> None:
    from datetime import datetime, timezone

    events = [
        {**dispatch_event("DEPENDENCY_RECHECK_CANDIDATE"), "_run_url": "https://example.invalid/1"},
        {**dispatch_event("DEPENDENCY_RECHECK_CANDIDATE"), "_run_url": "https://example.invalid/2"},
        {**dispatch_event("REPAIR_CANDIDATE"), "_run_url": "https://example.invalid/3"},
    ]
    window = build_dispatch_evidence_window(
        events,
        window_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert window is not None
    metric = window["metrics"]["cross_repo_incident_count"]
    assert metric["status"] == "MEASURED"
    assert metric["value"] == 2
    validate_window(window)  # every other metric must still satisfy the schema as UNKNOWN


def test_build_dispatch_window_returns_none_without_recheck_events() -> None:
    from datetime import datetime, timezone

    events = [{**dispatch_event("REPAIR_CANDIDATE"), "_run_url": "https://example.invalid/1"}]
    window = build_dispatch_evidence_window(
        events,
        window_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert window is None


def test_collect_dispatch_window_is_deterministic_on_replay() -> None:
    run = workflow_run(5, "2026-09-05T21:29:35Z")
    archive_url = "https://example.invalid/artifact.zip"
    event = dispatch_event("DEPENDENCY_RECHECK_CANDIDATE")
    transport = FakeTransport({
        f"{ORCH_ROOT}/actions/workflows/orchestrator-observe.yml/runs?event=workflow_dispatch&status=success&per_page=100": {
            "workflow_runs": [run]
        },
        f"{ORCH_ROOT}/actions/runs/5/artifacts": {
            "artifacts": [{"name": "n8n-event", "expired": False, "archive_download_url": archive_url}]
        },
    })
    downloader = FakeArtifactDownloader({archive_url: _zip_with_json("n8n-event.json", event)})
    store = {"schema_version": 1, "windows": []}
    first = collect_dispatch_window(transport, downloader, store, since="2026-01-01T00:00:00Z")
    second = collect_dispatch_window(transport, downloader, store, since="2026-01-01T00:00:00Z")
    assert validate_window(first)["window_id"] == validate_window(second)["window_id"]


def test_collect_portfolio_windows_includes_dispatch_window_by_default() -> None:
    run = workflow_run(6, "2026-09-05T21:29:35Z")
    archive_url = "https://example.invalid/artifact.zip"
    event = dispatch_event("DEPENDENCY_RECHECK_CANDIDATE")
    responses = {
        f"{ORCH_ROOT}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1": [],
        f"{ORCH_ROOT}/actions/workflows/orchestrator-observe.yml/runs?event=workflow_dispatch&status=success&per_page=100": {
            "workflow_runs": [run]
        },
        f"{ORCH_ROOT}/actions/runs/6/artifacts": {
            "artifacts": [{"name": "n8n-event", "expired": False, "archive_download_url": archive_url}]
        },
    }
    transport = FakeTransport(responses)
    downloader = FakeArtifactDownloader({archive_url: _zip_with_json("n8n-event.json", event)})
    store = {"schema_version": 1, "windows": []}
    windows = collect_portfolio_windows(
        transport, store,
        repositories=[ORCHESTRATOR_REPOSITORY],
        since="2026-01-01T00:00:00Z",
        downloader=downloader,
    )
    assert any(w["metrics"]["cross_repo_incident_count"]["status"] == "MEASURED" for w in windows)


def test_collect_repository_window_does_not_double_count_prs_on_overlapping_since() -> None:
    from project_orchestrator.timeseries import append_window

    pr = merged_pr(30, created_at="2026-09-05T00:00:00Z", merged_at="2026-09-05T01:00:00Z")
    checks = {"merge-sha-30": [check_run("test", "success", "2026-09-05T00:10:00Z")]}
    transport = FakeTransport(base_responses([pr], checks_by_sha=checks))
    store = {"schema_version": 1, "windows": []}

    first_window = collect_repository_window(transport, store, REPO, since="2026-01-01T00:00:00Z")
    store = append_window(store, first_window)

    # A second call with an earlier (overlapping) --since must not re-count
    # PR #30: it was already recorded in first_window's evidence_refs.
    second_window = collect_repository_window(transport, store, REPO, since="2020-01-01T00:00:00Z")
    assert second_window is None


def test_collect_dispatch_window_does_not_double_count_runs_on_overlapping_since() -> None:
    from project_orchestrator.timeseries import append_window

    run = workflow_run(7, "2026-09-05T21:29:35Z")
    archive_url = "https://example.invalid/artifact.zip"
    event = dispatch_event("DEPENDENCY_RECHECK_CANDIDATE")
    transport = FakeTransport({
        f"{ORCH_ROOT}/actions/workflows/orchestrator-observe.yml/runs?event=workflow_dispatch&status=success&per_page=100": {
            "workflow_runs": [run]
        },
        f"{ORCH_ROOT}/actions/runs/7/artifacts": {
            "artifacts": [{"name": "n8n-event", "expired": False, "archive_download_url": archive_url}]
        },
    })
    downloader = FakeArtifactDownloader({archive_url: _zip_with_json("n8n-event.json", event)})
    store = {"schema_version": 1, "windows": []}

    first_window = collect_dispatch_window(transport, downloader, store, since="2026-01-01T00:00:00Z")
    store = append_window(store, first_window)

    second_window = collect_dispatch_window(transport, downloader, store, since="2020-01-01T00:00:00Z")
    assert second_window is None


def test_collect_portfolio_windows_excludes_dispatch_window_when_disabled() -> None:
    responses = {
        f"{ORCH_ROOT}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1": [],
    }
    transport = FakeTransport(responses)
    store = {"schema_version": 1, "windows": []}
    windows = collect_portfolio_windows(
        transport, store,
        repositories=[ORCHESTRATOR_REPOSITORY],
        since="2026-01-01T00:00:00Z",
        include_dispatch_evidence=False,
    )
    assert windows == []
