"""Automatic Portfolio Evidence Collector.

Populates ``engineering/state/compounding-timeseries.json`` from reproducible
GitHub evidence (merged pull requests and their check-run history) instead
of requiring a human to hand-write evidence windows.

Design constraints carried over unchanged from ``timeseries``:

- deterministic: the same GitHub state always produces the same window
  content and therefore the same ``window_id``;
- idempotent: re-running over already-collected history reproduces the
  same windows, which ``timeseries.append_window`` already deduplicates;
- evidence-linked: every ``MEASURED`` metric carries the PR URLs it was
  computed from;
- fail closed: a metric this collector cannot reliably compute from GitHub
  history is left ``UNKNOWN`` — never approximated or defaulted to zero.

What this collector can and cannot measure is documented metric-by-metric
below and in ``docs/ARCHITECTURE.md``. Several of the thirteen
compounding-evidence metrics have no GitHub-observable signal at all
(Product Reality debt, preflight-catches-that-never-left the developer's
machine, human-intervention counts) and stay permanently ``UNKNOWN`` from
this module by design, not by omission.
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from .github_evidence import GitHubNotFound, Transport
from .inventory import EXPECTED_REPOSITORIES
from .timeseries import ALLOWED_METRIC_KEYS, recent_windows

DEFAULT_LOOKBACK = timedelta(days=30)
MAX_COMMITS_INSPECTED_PER_PR = 20
FAILURE_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
ORCHESTRATOR_REPOSITORY = "jweter/project-orchestrator"
DISPATCH_WORKFLOW_FILE = "orchestrator-observe.yml"
DISPATCH_ARTIFACT_NAME = "n8n-event"
DISPATCH_NOTES_PREFIX = "automatically collected from orchestrator-observe.yml"

# Repository -> portfolio project id, matching engineering/portfolio.json,
# plus the orchestrator's own repository under the synthetic "portfolio"
# project (it is not one of the five product repositories).
_REPO_TO_PROJECT: dict[str, str] = {
    "jweter/knowledge-engine-core": "knowledge-engine",
    "jweter/knowledge-engine-web": "knowledge-engine",
    "jweter/knowledge-engine-ai": "knowledge-engine",
    "jweter/rocksmith-cdlc-generator": "rocksmith",
    "jweter/Project-Everward": "everward",
    "jweter/project-orchestrator": "portfolio",
}
COLLECTIBLE_REPOSITORIES = frozenset(_REPO_TO_PROJECT)
assert EXPECTED_REPOSITORIES <= COLLECTIBLE_REPOSITORIES, "collector is missing a canonical product repository"


class EvidenceCollectorError(RuntimeError):
    """Raised when live evidence cannot be safely collected."""


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _repo_path(repository: str) -> str:
    owner, name = repository.split("/", 1)
    return f"/repos/{owner}/{name}"


def _paginate(transport: Transport, path: str, *, per_page: int = 100, max_pages: int = 20) -> list[Any]:
    items: list[Any] = []
    separator = "&" if "?" in path else "?"
    for page in range(1, max_pages + 1):
        payload = transport.get_json(f"{path}{separator}per_page={per_page}&page={page}")
        if not isinstance(payload, list):
            raise EvidenceCollectorError(f"expected list response while paginating {path}")
        items.extend(payload)
        if len(payload) < per_page:
            return items
    raise EvidenceCollectorError(f"pagination exceeded {max_pages} pages for {path}; evidence is incomplete")


def fetch_recently_merged_pull_requests(
    transport: Transport, repository: str, since: datetime
) -> list[dict[str, Any]]:
    """Merged PRs for ``repository`` with ``merged_at`` at/after ``since``.

    Fetches state=closed pulls sorted by most-recently-updated and stops
    paginating once a page's PRs are all older than ``since`` — closed PRs
    stop being touched once merged, so "updated_at" is a safe bound.
    """
    root = _repo_path(repository)
    matched: list[dict[str, Any]] = []
    for page in range(1, 11):
        payload = transport.get_json(
            f"{root}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page={page}"
        )
        if not isinstance(payload, list):
            raise EvidenceCollectorError(f"expected list response for closed PRs on {repository}")
        if not payload:
            break
        page_had_recent = False
        for pr in payload:
            updated_at = pr.get("updated_at")
            if updated_at and _parse_iso(updated_at) < since:
                continue
            page_had_recent = True
            merged_at = pr.get("merged_at")
            if merged_at and _parse_iso(merged_at) >= since:
                matched.append(pr)
        if not page_had_recent or len(payload) < 100:
            break
    return matched


def fetch_pr_commit_shas(transport: Transport, repository: str, number: int) -> list[str]:
    root = _repo_path(repository)
    commits = _paginate(transport, f"{root}/pulls/{number}/commits")
    shas = [c["sha"] for c in commits if isinstance(c, dict) and c.get("sha")]
    return shas[-MAX_COMMITS_INSPECTED_PER_PR:]


def fetch_check_runs(transport: Transport, repository: str, sha: str) -> list[dict[str, Any]]:
    root = _repo_path(repository)
    checks: list[dict[str, Any]] = []
    for page in range(1, 11):
        try:
            payload = transport.get_json(f"{root}/commits/{sha}/check-runs?per_page=100&page={page}")
        except GitHubNotFound:
            return checks
        if not isinstance(payload, dict) or not isinstance(payload.get("check_runs"), list):
            raise EvidenceCollectorError(f"malformed check-runs response for {repository}@{sha}")
        page_checks = payload["check_runs"]
        checks.extend(page_checks)
        if len(page_checks) < 100:
            break
    return checks


def analyze_merged_pr(transport: Transport, repository: str, pr: dict[str, Any]) -> dict[str, Any]:
    """Compute per-PR evidence. Any value the PR's own history cannot support is ``None``."""
    number = pr["number"]
    url = pr.get("html_url") or f"https://github.com/{repository}/pull/{number}"
    created_at = _parse_iso(pr["created_at"])
    merged_at = _parse_iso(pr["merged_at"])

    shas = fetch_pr_commit_shas(transport, repository, number)
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    head_sha = head.get("sha") or (shas[-1] if shas else None)

    all_checks: list[dict[str, Any]] = []
    for sha in shas:
        all_checks.extend(fetch_check_runs(transport, repository, sha))
    completed = [c for c in all_checks if c.get("status") == "completed" and c.get("completed_at")]
    failed = [c for c in completed if c.get("conclusion") in FAILURE_CONCLUSIONS]
    succeeded = [c for c in completed if c.get("conclusion") == "success"]

    failures_by_name = Counter(c.get("name") for c in failed)
    repeat_failure_count = sum(max(0, n - 1) for n in failures_by_name.values())

    head_checks = fetch_check_runs(transport, repository, head_sha) if head_sha else []
    head_completed = [c for c in head_checks if c.get("status") == "completed" and c.get("completed_at")]
    green_at: datetime | None = None
    if (
        head_checks
        and len(head_completed) == len(head_checks)
        and all(c.get("conclusion") == "success" for c in head_completed)
    ):
        green_at = max(_parse_iso(c["completed_at"]) for c in head_completed)

    pr_creation_to_green_seconds = (
        (green_at - created_at).total_seconds() if green_at and green_at > created_at else None
    )

    failure_to_repair_seconds = None
    if failed and green_at:
        first_failure_at = min(_parse_iso(c["completed_at"]) for c in failed)
        if green_at > first_failure_at:
            failure_to_repair_seconds = (green_at - first_failure_at).total_seconds()

    return {
        "number": number,
        "url": url,
        "merge_cycle_seconds": max(0.0, (merged_at - created_at).total_seconds()),
        "pr_creation_to_green_seconds": pr_creation_to_green_seconds,
        "ci_failure_count": len(failed),
        "ci_repeat_failure_count": repeat_failure_count,
        "failure_to_repair_seconds": failure_to_repair_seconds,
        "has_ci_evidence": bool(completed) or bool(head_completed),
    }


def _measured(value: float, unit: str, sample_count: int, evidence_refs: list[str]) -> dict[str, Any]:
    return {
        "status": "MEASURED",
        "value": value,
        "unit": unit,
        "sample_count": sample_count,
        "evidence_refs": sorted(set(evidence_refs)),
    }


def _unknown() -> dict[str, Any]:
    return {"status": "UNKNOWN"}


def _mean_metric(
    analyses: list[dict[str, Any]], key: str, unit: str
) -> dict[str, Any]:
    contributing = [a for a in analyses if a[key] is not None]
    if not contributing:
        return _unknown()
    total = sum(a[key] for a in contributing)
    refs = [a["url"] for a in contributing]
    return _measured(total / len(contributing), unit, len(contributing), refs)


def _sum_metric(analyses: list[dict[str, Any]], key: str) -> dict[str, Any]:
    with_ci_evidence = [a for a in analyses if a["has_ci_evidence"]]
    if not with_ci_evidence:
        return _unknown()
    total = sum(a[key] for a in with_ci_evidence)
    refs = [a["url"] for a in with_ci_evidence]
    return _measured(float(total), "count", len(with_ci_evidence), refs)


def build_evidence_window(
    project: str,
    repository: str,
    analyses: list[dict[str, Any]],
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    """Build one aggregated evidence window from a batch of analyzed merged PRs.

    Metrics with no GitHub-observable signal (blocked/waiting time,
    preflight-prevented count, Product Reality debt, cross-repo incidents,
    autonomous vs. Jeremy-required intervention counts) are always
    UNKNOWN here — this collector has no source of evidence for them, and
    saying so is the honest, fail-closed answer rather than a fabricated
    zero.
    """
    metrics: dict[str, Any] = {
        "pr_creation_to_green_seconds": _mean_metric(analyses, "pr_creation_to_green_seconds", "seconds"),
        "merge_cycle_seconds": _mean_metric(analyses, "merge_cycle_seconds", "seconds"),
        "failure_to_repair_seconds": _mean_metric(analyses, "failure_to_repair_seconds", "seconds"),
        "blocked_waiting_seconds": _unknown(),
        "ci_failure_count": _sum_metric(analyses, "ci_failure_count"),
        "ci_repeat_failure_count": _sum_metric(analyses, "ci_repeat_failure_count"),
        "preflight_failures_prevented_count": _unknown(),
        "roadmap_slices_completed_count": (
            _measured(float(len(analyses)), "count", len(analyses), [a["url"] for a in analyses])
            if repository == "jweter/project-orchestrator" and analyses
            else _unknown()
        ),
        "product_reality_debt_created_count": _unknown(),
        "product_reality_debt_resolved_count": _unknown(),
        "cross_repo_incident_count": _unknown(),
        "autonomous_intervention_count": _unknown(),
        "jeremy_required_intervention_count": _unknown(),
    }
    assert set(metrics) == set(ALLOWED_METRIC_KEYS), "evidence window must cover every declared metric key"

    return {
        "project": project,
        "repository": repository,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "notes": (
            f"automatically collected from {len(analyses)} merged pull request(s): "
            + ", ".join(f"#{a['number']}" for a in analyses)
        ),
        "metrics": metrics,
    }


def _resolve_since(
    store: dict[str, Any],
    project: str,
    repository: str,
    explicit_since: str | None,
    *,
    notes_prefix: str = "automatically collected from",
) -> datetime:
    if explicit_since is not None:
        return _parse_iso(explicit_since)
    existing = recent_windows(store, project=project, repository=repository)
    automatic = [
        w for w in existing
        if isinstance(w.get("notes"), str) and w["notes"].startswith(notes_prefix)
    ]
    if automatic:
        return _parse_iso(automatic[0]["window_end"])
    return datetime.now(timezone.utc) - DEFAULT_LOOKBACK


def _covered_evidence_refs(store: dict[str, Any], project: str, repository: str) -> set[str]:
    """Every evidence_ref already recorded by any prior window for this scope.

    Guards idempotency independently of ``since`` and window provenance:
    manually recorded seed evidence and automatically collected evidence
    represent the same underlying PR/run when their evidence references
    match, so both must suppress re-collection. Content-derived
    ``window_id`` dedup alone cannot catch overlapping windows.
    """
    refs: set[str] = set()
    for window in recent_windows(store, project=project, repository=repository):
        for metric in window.get("metrics", {}).values():
            if metric.get("status") == "MEASURED":
                refs.update(metric.get("evidence_refs", []))
    return refs


def collect_repository_window(
    transport: Transport,
    store: dict[str, Any],
    repository: str,
    *,
    since: str | None = None,
) -> dict[str, Any] | None:
    """Collect one new evidence window for ``repository``, or ``None`` if nothing new since the cutoff."""
    if repository not in COLLECTIBLE_REPOSITORIES:
        raise EvidenceCollectorError(f"{repository} is not a known portfolio repository")
    project = _REPO_TO_PROJECT[repository]
    cutoff = _resolve_since(store, project, repository, since)
    already_covered = _covered_evidence_refs(store, project, repository)

    merged_prs = fetch_recently_merged_pull_requests(transport, repository, cutoff)
    merged_prs = [pr for pr in merged_prs if pr.get("html_url") not in already_covered]
    if not merged_prs:
        return None

    analyses = [analyze_merged_pr(transport, repository, pr) for pr in merged_prs]
    analyses.sort(key=lambda a: a["number"])
    window_start = min(_parse_iso(pr["created_at"]) for pr in merged_prs)
    window_end = max(_parse_iso(pr["merged_at"]) for pr in merged_prs)
    if window_end <= window_start:
        window_end = window_start + timedelta(seconds=1)
    return build_evidence_window(project, repository, analyses, window_start=window_start, window_end=window_end)


class ArtifactDownloader(Protocol):
    def get_bytes(self, url: str) -> bytes: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, D102
        return None


@dataclass
class UrllibArtifactDownloader:
    """Downloads a GitHub Actions artifact zip.

    ``archive_download_url`` is an api.github.com endpoint that 302s to a
    signed, time-limited storage URL on a different host. That second
    request must **not** carry the GitHub bearer token — some signed-URL
    backends reject a request that presents two conflicting auth schemes —
    so redirects are followed manually rather than left to urllib's
    default (which would resend every header, including Authorization, to
    the new host).
    """

    token: str | None = None

    @classmethod
    def from_environment(cls) -> "UrllibArtifactDownloader":
        return cls(token=os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"))

    def get_bytes(self, url: str) -> bytes:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "project-orchestrator",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        opener = urllib.request.build_opener(_NoRedirectHandler)
        request = urllib.request.Request(url, headers=headers)
        try:
            with opener.open(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise
            location = exc.headers.get("Location") if exc.headers else None
            if not location:
                raise
            with urllib.request.urlopen(urllib.request.Request(location), timeout=60) as response:
                return response.read()


def _extract_json_member(archive_bytes: bytes, member_name: str) -> Any:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        with archive.open(member_name) as handle:
            return json.load(handle)


def fetch_dispatch_events(
    transport: Transport,
    downloader: ArtifactDownloader,
    since: datetime,
    *,
    limit_runs: int = 50,
) -> list[dict[str, Any]]:
    """Sanitized n8n dispatch evidence captured by orchestrator-observe.yml.

    Each successful workflow_dispatch run of that workflow uploads an
    ``n8n-event`` artifact containing exactly the sanitized fields
    ``engineering/capture_n8n_event.py`` wrote and validated — no raw
    webhook payload, signature, or credential ever reaches it. A run whose
    artifact has expired or cannot be parsed is skipped rather than
    aborting the whole collection (fail closed per-run, not per-portfolio).
    """
    root = _repo_path(ORCHESTRATOR_REPOSITORY)
    runs: list[dict[str, Any]] = []
    reached_cutoff = False
    for page in range(1, 21):
        runs_payload = transport.get_json(
            f"{root}/actions/workflows/{DISPATCH_WORKFLOW_FILE}/runs"
            f"?event=workflow_dispatch&status=success&per_page=100&page={page}"
        )
        if not isinstance(runs_payload, dict) or not isinstance(runs_payload.get("workflow_runs"), list):
            raise EvidenceCollectorError("malformed workflow run list for orchestrator-observe.yml")
        page_runs = runs_payload["workflow_runs"]
        if not page_runs:
            reached_cutoff = True
            break
        for run in page_runs:
            created_at = run.get("created_at")
            if created_at and _parse_iso(created_at) < since:
                reached_cutoff = True
                break
            runs.append(run)
            if len(runs) > limit_runs:
                raise EvidenceCollectorError(
                    f"dispatch evidence exceeds safety limit ({limit_runs}) before cutoff; evidence is incomplete"
                )
        if reached_cutoff or len(page_runs) < 100:
            reached_cutoff = True
            break
    if not reached_cutoff:
        raise EvidenceCollectorError("dispatch workflow pagination exceeded 20 pages before cutoff")

    events: list[dict[str, Any]] = []
    for run in runs:
        created_at = run.get("created_at")
        if not created_at:
            continue
        run_id = run.get("id")
        if run_id is None:
            continue
        artifacts_payload = transport.get_json(f"{root}/actions/runs/{run_id}/artifacts")
        if not isinstance(artifacts_payload, dict) or not isinstance(artifacts_payload.get("artifacts"), list):
            continue
        artifact = next(
            (a for a in artifacts_payload["artifacts"] if a.get("name") == DISPATCH_ARTIFACT_NAME),
            None,
        )
        if artifact is None or artifact.get("expired") or not artifact.get("archive_download_url"):
            continue
        try:
            payload = _extract_json_member(
                downloader.get_bytes(artifact["archive_download_url"]), f"{DISPATCH_ARTIFACT_NAME}.json"
            )
        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            continue
        payload = dict(payload)
        payload["_run_url"] = run.get("html_url") or f"https://github.com/{ORCHESTRATOR_REPOSITORY}/actions/runs/{run_id}"
        events.append(payload)
    return events


def build_dispatch_evidence_window(
    events: list[dict[str, Any]], *, window_start: datetime, window_end: datetime
) -> dict[str, Any] | None:
    """Do not infer cross-repository incidents from recheck-candidate dispatches.

    DEPENDENCY_RECHECK_CANDIDATE means only that a Knowledge Engine
    main-branch change should be rechecked. It is not evidence that a
    compatibility incident occurred. Until the dispatch artifact records a
    deterministic recheck outcome, this source cannot populate any metric,
    so it deliberately emits no evidence window.
    """
    _ = (events, window_start, window_end)
    return None


def collect_dispatch_window(
    transport: Transport,
    downloader: ArtifactDownloader,
    store: dict[str, Any],
    *,
    since: str | None = None,
) -> dict[str, Any] | None:
    cutoff = _resolve_since(
        store, "knowledge-engine", ORCHESTRATOR_REPOSITORY, since, notes_prefix=DISPATCH_NOTES_PREFIX
    )
    already_covered = _covered_evidence_refs(store, "knowledge-engine", ORCHESTRATOR_REPOSITORY)
    events = fetch_dispatch_events(transport, downloader, cutoff)
    events = [e for e in events if e.get("_run_url") not in already_covered]
    if not events:
        return None
    created_ats = [_parse_iso(e["received_at"]) for e in events if e.get("received_at")]
    if not created_ats:
        return None
    window_start = min(created_ats)
    window_end = max(created_ats)
    if window_end <= window_start:
        window_end = window_start + timedelta(seconds=1)
    return build_dispatch_evidence_window(events, window_start=window_start, window_end=window_end)


def collect_portfolio_windows(
    transport: Transport,
    store: dict[str, Any],
    *,
    repositories: list[str] | None = None,
    since: str | None = None,
    downloader: ArtifactDownloader | None = None,
    include_dispatch_evidence: bool = True,
) -> list[dict[str, Any]]:
    """Collect at most one new window per repository, plus one dispatch-evidence window.

    Never mutates ``store``. The dispatch-evidence window (cross-repo
    incidents observed via orchestrator-observe.yml artifacts) is skipped
    when ``include_dispatch_evidence`` is false or when the requested
    ``repositories`` explicitly excludes the orchestrator's own repository,
    since collecting it always queries project-orchestrator's own Actions
    history regardless of which product repository windows were asked for.
    """
    targets = repositories if repositories is not None else sorted(COLLECTIBLE_REPOSITORIES)
    unknown = sorted(set(targets) - COLLECTIBLE_REPOSITORIES)
    if unknown:
        raise EvidenceCollectorError(f"unknown repositories requested: {unknown}")

    windows = []
    for repository in sorted(targets):
        window = collect_repository_window(transport, store, repository, since=since)
        if window is not None:
            windows.append(window)

    if include_dispatch_evidence and ORCHESTRATOR_REPOSITORY in targets:
        active_downloader = downloader or UrllibArtifactDownloader.from_environment()
        dispatch_window = collect_dispatch_window(transport, active_downloader, store, since=since)
        if dispatch_window is not None:
            windows.append(dispatch_window)
    return windows
