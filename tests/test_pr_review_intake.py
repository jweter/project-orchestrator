"""The existing PR watcher may observe review work, never authorize execution."""
from __future__ import annotations

import json

from scripts import pr_review_intake, failed_pr_accountability

HEAD = "a" * 40
REPO = "jweter/Project-Everward"


def pr():
    return {"author": {"login": "jweter"}, "title": "Iron God [RM-example]: review",
            "number": 422, "headRefOid": HEAD, "statusCheckRollup": []}


def threads():
    return [{"id": "T1", "resolved": False,
             "path": "tools/playtest/classification_tally.py", "line": 8,
             "comments": [{"commit": HEAD, "body": "P2: fix non-string values; do not skip them"}]}]


def test_intake_records_review_once_without_granting_execution(tmp_path):
    first = pr_review_intake.observe(REPO, pr(), tmp_path, fetch_threads=lambda r, n: threads())
    assert first["state"] == "REVIEW_INTAKE_RECORDED"
    path = tmp_path / "pr-review-intake" / "jweter--Project-Everward--422.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    assert receipt["head_sha"] == HEAD
    assert receipt["status"] == "REVIEW_SCOPE_VALIDATION_REQUIRED"
    assert receipt["dispatchable"] is False
    assert receipt["attempt_budget_reset"] is False
    assert receipt["advisory_only"] is True
    assert receipt["findings"][0]["path"] == "tools/playtest/classification_tally.py"
    assert "non-string" not in path.read_text(encoding="utf-8")
    previous = path.read_bytes()
    second = pr_review_intake.observe(REPO, pr(), tmp_path, fetch_threads=lambda r, n: threads())
    assert second["state"] == "REVIEW_INTAKE_UNCHANGED"
    assert path.read_bytes() == previous


def test_review_observation_fail_closed_when_truncated_or_unavailable(tmp_path):
    def unavailable(r, n):
        raise RuntimeError("REVIEW_THREADS_INCOMPLETE")
    result = pr_review_intake.observe(REPO, pr(), tmp_path, fetch_threads=unavailable)
    assert result["state"] == "REVIEW_INTAKE_EVIDENCE_UNVERIFIED"
    assert not list(tmp_path.rglob("*.json"))


def test_review_observation_does_not_adopt_untrusted_or_unrelated_pr(tmp_path):
    a = pr()
    a["author"] = {"login": "dependabot[bot]"}
    assert pr_review_intake.observe(REPO, a, tmp_path, fetch_threads=lambda r,n: threads()) is None
    a["author"] = {"login": "jweter"}
    a["title"] = "Personal PR unrelated to Iron God"
    assert pr_review_intake.observe(REPO, a, tmp_path, fetch_threads=lambda r,n: threads()) is None
    assert not list(tmp_path.rglob("*.json"))


def test_existing_controller_includes_review_intake_without_new_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(failed_pr_accountability, "PORTFOLIO_REPOS", (REPO,))
    monkeypatch.setattr(failed_pr_accountability, "open_prs", lambda repo: [pr()])
    monkeypatch.setattr(pr_review_intake.green_pr_merger, "review_threads", lambda r, n: threads())
    result = failed_pr_accountability.cycle(tmp_path, tmp_path)
    assert len(result) == 1
    assert result[0]["state"] == "REVIEW_INTAKE_RECORDED"
    assert failed_pr_accountability.cycle(tmp_path, tmp_path) == []

def test_resolved_review_never_leaves_stale_dispatchable_intake(tmp_path):
    pr_review_intake.observe(REPO, pr(), tmp_path, fetch_threads=lambda r, n: threads())
    path = tmp_path / "pr-review-intake" / "jweter--Project-Everward--422.json"
    cleared = pr_review_intake.observe(REPO, pr(), tmp_path, fetch_threads=lambda r, n: [
        {"id": "T1", "resolved": True, "path": "tools/playtest/classification_tally.py",
         "line": 8, "comments": [{"commit": HEAD, "body": "P2 prior finding"}]}
    ])
    assert cleared["state"] == "REVIEW_INTAKE_NO_CURRENT_HEAD_FINDINGS"
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["status"] == "NO_CURRENT_HEAD_FINDINGS"
    assert record["findings"] == []
    assert record["dispatchable"] is False
