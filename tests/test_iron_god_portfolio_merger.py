"""Autonomous portfolio merger: deterministic policy, exact-head merge, durable evidence, no human approval."""

from __future__ import annotations

import copy
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from project_orchestrator import iron_god_portfolio_merger as pm
from project_orchestrator import iron_god_roadmap_refill as refill

REPO = "jweter/knowledge-engine-core"
NOW = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)
HEAD, MAIN = "a" * 40, "b" * 40
IDENT = refill.item_id(REPO, "cap-cache", "s1")
TITLE = f"Iron God [{IDENT}] Bound the cache"
PLAN_TITLE = f"Iron God [{refill.id_prefix(REPO)}plan-9c998232] Plan next slices"
INHERITED = list(refill.DEFAULT_FORBIDDEN)


def slice_row(sid: str = "s1", **over: Any) -> dict:
    row = {"id": sid, "state": "PLANNED", "capability_level": 3, "objective": "Bound the cache by entry count.",
           "acceptance": ["pruning keeps the newest entries"], "allowed_paths": ["pkg/**", "tests/**"],
           "forbidden_paths": ["pkg/secret/**", *INHERITED]}
    row.update(over)
    return row


def graph(slices: list[dict] | None = None) -> dict:
    cap = {"id": "cap-cache", "capability": "Bounded cache", "state": "PLANNED", "depends_on": [],
           "completion_evidence": ["docs/ROADMAP.md cache section"], "slices": slices or [slice_row()]}
    return {"schema_version": 1, "repository": REPO, "capabilities": [cap], "roadmap": ["cap-cache"]}


def file_row(path: str, patch: str | None = "+value = 1\n", status: str = "modified", add: int = 1, dele: int = 0) -> dict:
    return {"path": path, "status": status, "additions": add, "deletions": dele, "patch": patch}


def checks(**state: str) -> list[dict]:
    rows = [{"name": "Canonical preflight", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "Python quality checks", "status": "COMPLETED", "conclusion": "SUCCESS"}]
    for row in rows:
        row.update(state)
    return rows


def facts(**over: Any) -> dict:
    base = {"repository": REPO, "number": 7, "title": TITLE, "author": "jweter", "draft": False, "state": "OPEN",
            "base": "main", "head": HEAD, "merge_state": "CLEAN", "review_decision": None,
            "created_at": (NOW - timedelta(hours=2)).isoformat(), "checks": checks(),
            "files": [file_row("pkg/cache.py"), file_row("tests/test_cache.py")], "threads": [],
            "quiet_since": (NOW - timedelta(minutes=10)).isoformat(), "main_sha": MAIN,
            "graph_base": json.dumps(graph()), "graph_head": None}
    base.update(over)
    return base


def verdict(**over: Any) -> pm.Verdict:
    return pm.evaluate(facts(**over), now=NOW)


# ---- policy: the happy path and every gate ------------------------------------------------------------------------


def test_a_fully_verified_roadmap_pull_request_merges_and_a_draft_is_promoted_first() -> None:
    merge = verdict()
    assert merge.action == pm.MERGE and merge.authorization["slice"] == "cap-cache/s1"
    assert merge.authorization["graph_main_sha"] == MAIN and merge.authorization["checks"]["preflight_success"]
    assert verdict(draft=True).action == pm.PROMOTE  # draft: verified, but the review window starts at promotion


@pytest.mark.parametrize("over, action, fragment", [
    ({"checks": checks(conclusion="FAILURE")}, pm.REFUSE, "CHECK_FAILED"),
    ({"checks": [{"name": "Canonical preflight", "status": "IN_PROGRESS"}]}, pm.WAIT, "CHECKS_PENDING"),
    ({"checks": []}, pm.WAIT, "CHECKS_MISSING"),
    ({"checks": [{"name": "Python quality checks", "status": "COMPLETED", "conclusion": "SUCCESS"}]},
     pm.REFUSE, "PREFLIGHT_EVIDENCE_MISSING"),
    ({"checks": [{"name": "Canonical preflight", "status": "COMPLETED", "conclusion": "SKIPPED"}]},
     pm.REFUSE, "PREFLIGHT_EVIDENCE_MISSING"),
    ({"checks": [{"context": "ci/status", "state": "PENDING"}, *checks()]}, pm.WAIT, "CHECKS_PENDING"),
    ({"threads": [{"id": "t", "resolved": False}]}, pm.REFUSE, "UNRESOLVED_REVIEW_THREADS"),
    ({"review_decision": "CHANGES_REQUESTED"}, pm.REFUSE, "REVIEW_CHANGES_REQUESTED"),
    ({"merge_state": "BEHIND"}, pm.WAIT, "MERGE_STATE_BEHIND"),
    ({"merge_state": "BLOCKED"}, pm.WAIT, "MERGE_STATE_BLOCKED"),
    ({"merge_state": "DIRTY"}, pm.REFUSE, "MERGE_CONFLICT"),
    ({"quiet_since": (NOW - timedelta(minutes=2)).isoformat()}, pm.WAIT, "REVIEW_SETTLE_WINDOW"),
    ({"quiet_since": None}, pm.WAIT, "REVIEW_ACTIVITY_UNKNOWN"),
    ({"base": "release"}, pm.REFUSE, "BASE_NOT_MAIN"),
])
def test_every_verification_gate_blocks_or_defers_a_merge(over: dict, action: str, fragment: str) -> None:
    result = verdict(**over)
    assert result.action == action and any(fragment in r for r in result.reasons), result


def test_the_quiet_window_does_not_apply_to_a_draft_but_a_failing_draft_is_never_promoted() -> None:
    assert verdict(draft=True, quiet_since=NOW.isoformat()).action == pm.PROMOTE
    assert verdict(draft=True, checks=checks(conclusion="FAILURE")).action == pm.REFUSE


# ---- policy: authorization from the graph on main, never from the pull request --------------------------------------


@pytest.mark.parametrize("suffix", ["", "-t1", "-r2", "-cap", "-t1-r1"])
def test_revisions_subtasks_and_escalations_resolve_to_the_parent_slice(suffix: str) -> None:
    title = f"Iron God [{IDENT}{suffix}] x"
    result = verdict(title=title)
    assert result.action == pm.MERGE and result.authorization["slice"] == "cap-cache/s1"


@pytest.mark.parametrize("files, fragment", [
    ([file_row("docs/other.md")], "OUT_OF_SCOPE_PATHS:docs/other.md"),
    ([file_row("pkg/secret/key.py")], "OUT_OF_SCOPE_PATHS:pkg/secret/key.py"),
    ([file_row("pkg/cache.py"), file_row("engineering/control-plane.json")], "OUT_OF_SCOPE_PATHS:engineering"),
])
def test_a_change_outside_the_slice_scope_is_escalated_to_the_owner(files: list, fragment: str) -> None:
    result = verdict(files=files)
    assert result.action == pm.ESCALATE and any(fragment in r for r in result.reasons), result


def test_an_unknown_slice_or_missing_graph_is_escalated_not_guessed() -> None:
    assert verdict(title="Iron God [RM-knowledge-engine-core-ghost-slice-12345678] x").reasons == (
        "SLICE_NOT_IN_AUTHORIZED_GRAPH",)
    assert verdict(graph_base=None).reasons == ("AUTHORITY_GRAPH_UNAVAILABLE",)
    assert verdict(graph_base="{not json").reasons == ("AUTHORITY_GRAPH_INVALID",)
    assert verdict(graph_base=json.dumps({"schema_version": 1, "capabilities": []})).action == pm.ESCALATE


def test_only_owner_account_roadmap_pull_requests_are_considered() -> None:
    assert verdict(author="someone-else").action == pm.ESCALATE
    assert verdict(title="Bump pytest", author="dependabot[bot]").reasons == ("DEPENDENCY_UPDATE_REQUIRES_OWNER",)
    assert verdict(title="Bump pytest", author="app/dependabot").action == pm.ESCALATE
    assert verdict(title="Fix a typo", author="jweter").action == pm.IGNORE
    assert verdict(state="CLOSED").action == pm.IGNORE


# ---- policy: reading the diff -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("files, fragment", [
    ([file_row("pkg/cache.py"), file_row(".github/workflows/ci.yml")], "SECURITY_SENSITIVE_PATH:.github/workflows/ci.yml"),
    ([file_row("pkg/cache.py"), file_row("pyproject.toml")], "DEPENDENCY_CHANGE_NEEDS_OWNER:pyproject.toml"),
    ([file_row("pkg/cache.py"), file_row("pkg/requirements-dev.txt")], "DEPENDENCY_CHANGE_NEEDS_OWNER"),
    ([file_row("pkg/cache.py", patch='+token = "ghp_' + "a" * 36 + '"\n')], "SECRET_PATTERN_IN_DIFF:pkg/cache.py"),
    ([file_row("pkg/cache.py", patch="+-----BEGIN RSA PRIVATE KEY-----\n")], "SECRET_PATTERN_IN_DIFF"),
    ([file_row("pkg/cache.py", patch='+api_key = "' + "x" * 30 + '"\n')], "SECRET_PATTERN_IN_DIFF"),
    ([file_row("pkg/cache.py"), file_row("tests/test_cache.py", patch=None, status="removed", add=0, dele=9)],
     "TEST_FILE_DELETED:tests/test_cache.py"),
    ([file_row("pkg/cache.py", patch=None, add=500)], "DIFF_UNAVAILABLE:pkg/cache.py"),
    ([file_row("pkg/cache.py", add=pm.MAX_ADDED_LINES + 1)], "DIFF_TOO_LARGE"),
    ([file_row(f"pkg/m{i}.py") for i in range(pm.MAX_FILES + 1)], "DIFF_TOO_MANY_FILES"),
])
def test_a_suspicious_diff_is_escalated_to_the_owner_even_when_every_check_is_green(files: list, fragment: str) -> None:
    result = verdict(files=files)
    assert result.action == pm.ESCALATE and any(fragment in r for r in result.reasons), result


def test_an_empty_diff_is_refused_and_removed_lines_alone_are_fine() -> None:
    assert verdict(files=[]).reasons == ("EMPTY_DIFF",)
    assert verdict(files=[file_row("pkg/old.py", patch="-gone = 1\n", status="modified", add=0, dele=1)]).action == pm.MERGE


def test_the_diff_digest_is_stable_and_sensitive_to_content() -> None:
    a, b = [file_row("pkg/a.py", "+1\n")], [file_row("pkg/a.py", "+2\n")]
    assert pm.diff_digest(a) == pm.diff_digest(copy.deepcopy(a)) != pm.diff_digest(b)


# ---- policy: planning pull requests edit the graph that authorizes future work -----------------------------------------


def planning(head_graph: dict, files: list | None = None, **over: Any) -> pm.Verdict:
    rows = files if files is not None else [file_row(pm.GRAPH_PATH, patch="+graph\n")]
    return verdict(title=PLAN_TITLE, files=rows, graph_head=json.dumps(head_graph), **over)


def with_new_slice(**over: Any) -> dict:
    head = graph([slice_row(), slice_row("s2", **over)])
    return head


def test_a_planning_pull_request_may_only_add_slices_to_the_graph() -> None:
    result = planning(with_new_slice())
    assert result.action == pm.MERGE and result.authorization["kind"] == "planning"
    assert planning(with_new_slice(), files=[file_row(pm.GRAPH_PATH), file_row("pkg/x.py")]).action == pm.ESCALATE
    assert planning(with_new_slice(), files=[file_row("pkg/x.py")]).action == pm.ESCALATE  # not the graph at all


@pytest.mark.parametrize("make_head, fragment", [
    (lambda: graph([slice_row(allowed_paths=["**"])]), "SLICE_AUTHORITY_EDITED:cap-cache/s1.allowed_paths"),
    (lambda: graph([slice_row(forbidden_paths=[])]), "SLICE_AUTHORITY_EDITED:cap-cache/s1.forbidden_paths"),
    (lambda: graph([slice_row(capability_level=1)]), "SLICE_AUTHORITY_EDITED:cap-cache/s1.capability_level"),
    (lambda: graph([slice_row("other")]), "SLICE_REMOVED:cap-cache/s1"),
    (lambda: with_new_slice(allowed_paths=[".github/workflows/**"]), "NEW_SLICE_SCOPE_TOO_BROAD"),
    (lambda: with_new_slice(allowed_paths=["**"]), "NEW_SLICE_SCOPE_TOO_BROAD"),
    (lambda: with_new_slice(allowed_paths=[".env*"]), "NEW_SLICE_SCOPE_TOO_BROAD"),
    (lambda: with_new_slice(allowed_paths=[]), "NEW_SLICE_WITHOUT_SCOPE"),
    (lambda: with_new_slice(forbidden_paths=["pkg/secret/**"]), "NEW_SLICE_FORBIDDEN_NOT_INHERITED"),
])
def test_a_planning_pull_request_cannot_rewrite_or_widen_authorization(make_head, fragment: str) -> None:
    result = planning(make_head())
    assert result.action == pm.REFUSE and any(fragment in r for r in result.reasons), result


def test_a_graph_that_does_not_validate_or_cannot_be_read_at_the_head_is_refused() -> None:
    assert planning({"schema_version": 1}).reasons == ("GRAPH_AT_HEAD_INVALID",)
    broken = verdict(title=PLAN_TITLE, files=[file_row(pm.GRAPH_PATH)], graph_head=None)
    assert broken.reasons == ("GRAPH_AT_HEAD_INVALID",)


def test_a_slice_pull_request_may_record_delivery_in_the_graph_but_never_rewrite_authority() -> None:
    delivered = graph([slice_row(state="VERIFIED_COMPLETE", verification=["pytest -q"])])
    delivered["capabilities"][0].update(state="VERIFIED_COMPLETE", verification=["pytest -q"])
    files = [file_row("pkg/cache.py"), file_row(pm.GRAPH_PATH)]
    ok = verdict(files=files, graph_head=json.dumps(delivered))
    assert ok.action == pm.MERGE, ok
    widened = graph([slice_row(allowed_paths=["**"])])
    assert verdict(files=files, graph_head=json.dumps(widened)).action == pm.REFUSE
    regressed_base = graph([slice_row(state="VERIFIED_COMPLETE", verification=["pytest -q"])])
    reopened = graph([slice_row(state="PLANNED", verification=["pytest -q"])])
    assert "SLICE_STATE_REGRESSED:cap-cache/s1" in pm.graph_change_problems(regressed_base, reopened)
    rewritten = graph([slice_row(state="VERIFIED_COMPLETE", verification=["something else"])])
    assert "SLICE_EVIDENCE_REWRITTEN:cap-cache/s1.verification" in pm.graph_change_problems(regressed_base, rewritten)


def test_the_repository_list_matches_the_portfolio_merger_script() -> None:
    from scripts import green_pr_merger

    assert pm.PORTFOLIO_REPOS == green_pr_merger.PORTFOLIO_REPOS


# ---- the cycle: discover, promote, re-check, merge exact head, record --------------------------------------------------


class FakePort:
    def __init__(self, **over: Any) -> None:
        self.pulls: dict[str, list[dict]] = {REPO: [self.summary()]}
        self.files_rows = [file_row("pkg/cache.py"), file_row("tests/test_cache.py")]
        self.graph_base: str | None = json.dumps(graph())
        self.graph_head_text: str | None = None
        self.pull_states: dict[tuple[str, int], str] = {}
        self.thread_rows: list[dict] = []
        self.quiet = (NOW - timedelta(minutes=10)).isoformat()
        self.calls: list[tuple] = []
        self.merge_error: str | None = None
        self.on_relist: Any = None
        self.list_error: set[str] = set()
        self.files_error = False
        self.__dict__.update(over)

    @staticmethod
    def summary(**over: Any) -> dict:
        row = {"number": 7, "title": TITLE, "isDraft": False, "headRefOid": HEAD, "baseRefName": "main",
               "author": {"login": "jweter"}, "createdAt": (NOW - timedelta(hours=2)).isoformat(),
               "mergeStateStatus": "CLEAN", "reviewDecision": "", "statusCheckRollup": checks(), "state": "OPEN"}
        row.update(over)
        return row

    def list_open(self, repo: str) -> list[dict]:
        self.calls.append(("list", repo))
        if repo in self.list_error:
            raise RuntimeError("GH_FAILED")
        if self.on_relist and sum(1 for c in self.calls if c[0] == "list") > 1:
            self.on_relist(self)
        return copy.deepcopy(self.pulls.get(repo, []))

    def files(self, repo: str, number: int) -> list[dict]:
        if self.files_error:
            raise RuntimeError("GH_FAILED")
        return copy.deepcopy(self.files_rows)

    def file_text(self, repo: str, path: str, ref: str) -> str | None:
        return self.graph_base if ref == "main" else self.graph_head_text

    def pull_state(self, repo: str, number: int) -> str:
        self.calls.append(("pull_state", repo, number))
        return self.pull_states.get((repo, number), "MERGED")

    def main_sha(self, repo: str) -> str:
        return MAIN

    def threads(self, repo: str, number: int) -> list[dict]:
        return copy.deepcopy(self.thread_rows)

    def quiet_since(self, repo: str, number: int, head: str, created_at: str) -> str | None:
        return self.quiet

    def mark_ready(self, repo: str, number: int) -> None:
        self.calls.append(("ready", repo, number))
        self.pulls[repo][0]["isDraft"] = False
        self.quiet = NOW.isoformat()  # ready_for_review restarts the review window

    def merge(self, repo: str, number: int, head: str) -> None:
        self.calls.append(("merge", repo, number, head))
        if self.merge_error:
            raise RuntimeError(self.merge_error)
        self.pulls[repo] = [p for p in self.pulls[repo] if p["number"] != number]

    def merged_info(self, repo: str, number: int) -> dict:
        return {"state": "MERGED", "mergeCommit": {"oid": "c" * 40}}


def run(tmp_path: Path, port: FakePort, *, at: datetime = NOW, **kw: Any) -> list[dict]:
    return pm.cycle(tmp_path, repositories=(REPO,), port=port, clock=lambda: at, **kw)


def ledger(tmp_path: Path) -> list[dict]:
    path = tmp_path / "portfolio-merger" / "decisions.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_a_ready_verified_pull_request_is_squash_merged_on_the_exact_head_with_durable_evidence(tmp_path: Path) -> None:
    port = FakePort()
    (row,) = run(tmp_path, port)
    assert row["merged"] is True and row["decision"] == pm.MERGE and row["head"] == HEAD
    assert ("merge", REPO, 7, HEAD) in port.calls
    (entry,) = ledger(tmp_path)
    assert entry["action"] == "MERGED" and entry["head"] == HEAD and entry["merge_commit"] == "c" * 40
    assert entry["authorization"]["slice"] == "cap-cache/s1" and entry["authorization"]["graph_main_sha"] == MAIN
    assert entry["files"] == ["pkg/cache.py", "tests/test_cache.py"] and len(entry["diff_sha256"]) == 64
    assert entry["policy"] == pm.POLICY_VERSION and entry["reasons"] == ["ALL_GATES_PASSED"]
    status = json.loads((tmp_path / "portfolio-merger" / "status.json").read_text(encoding="utf-8"))
    assert status["rows"][0]["merged"] is True


def test_a_draft_is_promoted_then_waits_out_the_review_window_before_merging(tmp_path: Path) -> None:
    port = FakePort()
    port.pulls[REPO] = [FakePort.summary(isDraft=True)]
    (row,) = run(tmp_path, port)
    assert row["promoted"] is True and not row["merged"] and ("ready", REPO, 7) in port.calls
    (row,) = run(tmp_path, port, at=NOW + timedelta(minutes=2))
    assert row["decision"] == pm.WAIT and "REVIEW_SETTLE_WINDOW" in row["reasons"]
    assert not any(c[0] == "merge" for c in port.calls)
    (row,) = run(tmp_path, port, at=NOW + timedelta(minutes=6))
    assert row["merged"] is True
    assert [e["action"] for e in ledger(tmp_path)] == ["PROMOTED", "MERGED"]


def test_a_head_that_changes_after_the_decision_is_never_merged(tmp_path: Path) -> None:
    port = FakePort()
    port.on_relist = lambda p: p.pulls[REPO][0].update(headRefOid="d" * 40)
    (row,) = run(tmp_path, port)
    assert row["decision"] == "HEAD_CHANGED_BEFORE_MERGE" and not row["merged"]
    assert not any(c[0] == "merge" for c in port.calls)


def test_conditions_are_rechecked_immediately_before_merging(tmp_path: Path) -> None:
    port = FakePort()
    port.on_relist = lambda p: p.pulls[REPO][0].update(statusCheckRollup=checks(conclusion="FAILURE"))
    (row,) = run(tmp_path, port)
    assert row["decision"] == pm.REFUSE and not row["merged"] and not any(c[0] == "merge" for c in port.calls)
    assert [e["action"] for e in ledger(tmp_path)] == ["REFUSE"]


def test_a_refusal_is_recorded_once_per_head_and_reason_and_a_new_head_is_recorded_again(tmp_path: Path) -> None:
    port = FakePort(thread_rows=[{"id": "t1", "resolved": False}])
    for _ in range(3):
        (row,) = run(tmp_path, port)
    assert row["decision"] == pm.REFUSE and not any(c[0] == "merge" for c in port.calls)
    assert len(ledger(tmp_path)) == 1 and ledger(tmp_path)[0]["reasons"] == ["UNRESOLVED_REVIEW_THREADS:1"]
    port.pulls[REPO][0]["headRefOid"] = "e" * 40
    run(tmp_path, port)
    assert len(ledger(tmp_path)) == 2


def test_an_escalation_is_recorded_as_needing_the_owner_and_nothing_is_changed(tmp_path: Path) -> None:
    port = FakePort(files_rows=[file_row("pkg/cache.py"), file_row("pyproject.toml")])
    (row,) = run(tmp_path, port)
    assert row["decision"] == pm.ESCALATE and not row["merged"]
    (entry,) = ledger(tmp_path)
    assert entry["needs_owner"] is True and any("DEPENDENCY_CHANGE_NEEDS_OWNER" in r for r in entry["reasons"])
    assert [c[0] for c in port.calls] == ["list"]


def test_waiting_and_ignored_pull_requests_leave_no_ledger_noise(tmp_path: Path) -> None:
    port = FakePort()
    port.pulls[REPO] = [FakePort.summary(statusCheckRollup=[{"name": "Canonical preflight", "status": "IN_PROGRESS"}]),
                        FakePort.summary(number=8, title="Unrelated change")]
    rows = run(tmp_path, port)
    assert [r["decision"] for r in rows] == [pm.WAIT, pm.IGNORE]
    assert ledger(tmp_path) == []


def test_a_dependency_bot_pull_request_is_escalated_never_merged(tmp_path: Path) -> None:
    port = FakePort()
    port.pulls[REPO] = [FakePort.summary(number=9, title="build(deps-dev): bump pytest", author={"login": "app/dependabot"})]
    (row,) = run(tmp_path, port)
    assert row["decision"] == pm.ESCALATE and not any(c[0] == "merge" for c in port.calls)
    assert ledger(tmp_path)[0]["reasons"] == ["DEPENDENCY_UPDATE_REQUIRES_OWNER"]


def test_a_failed_merge_is_recorded_and_other_pull_requests_still_proceed(tmp_path: Path) -> None:
    port = FakePort(merge_error="GH_FAILED: protected branch")
    (row,) = run(tmp_path, port)
    assert row["decision"] == "MERGE_FAILED" and not row["merged"]
    assert ledger(tmp_path)[0]["action"] == "MERGE_FAILED" and "protected branch" in ledger(tmp_path)[0]["error"]
    port = FakePort(files_error=True)
    port.pulls[REPO] = [FakePort.summary(), FakePort.summary(number=8, title="Unrelated change")]
    rows = run(tmp_path / "second", port)
    assert [r["decision"] for r in rows] == ["PR_PROCESSING_FAILED", pm.IGNORE]


def test_a_repository_that_cannot_be_read_is_reported_and_the_rest_continue(tmp_path: Path) -> None:
    port = FakePort(list_error={"jweter/other"})
    rows = pm.cycle(tmp_path, repositories=("jweter/other", REPO), port=port, clock=lambda: NOW)
    assert rows[0]["decision"] == "REPOSITORY_QUERY_FAILED" and rows[1]["merged"] is True


def test_a_dry_run_reads_everything_and_changes_nothing(tmp_path: Path) -> None:
    port = FakePort()
    (row,) = run(tmp_path, port, dry_run=True)
    assert row["decision"] == pm.MERGE and row["merged"] is False
    assert not any(c[0] in {"ready", "merge"} for c in port.calls)
    assert not (tmp_path / "portfolio-merger").exists()


# ---- the optional local-model advisor can only make the decision stricter ----------------------------------------------


def test_an_advisor_concern_downgrades_a_merge_to_an_escalation(tmp_path: Path) -> None:
    port = FakePort()
    (row,) = run(tmp_path, port, advisor=lambda f, v: {"concern": True, "notes": "removes a safety check"})
    assert row["decision"] == pm.ESCALATE and not any(c[0] == "merge" for c in port.calls)
    entry = ledger(tmp_path)[0]
    assert entry["reasons"] == ["ADVISOR_CONCERN"] and entry["advisor"]["notes"] == "removes a safety check"


def test_an_advisor_can_never_authorize_what_the_policy_refused(tmp_path: Path) -> None:
    calls: list[Any] = []
    port = FakePort(thread_rows=[{"id": "t", "resolved": False}])
    (row,) = run(tmp_path, port, advisor=lambda f, v: calls.append(v) or {"concern": False})
    assert row["decision"] == pm.REFUSE and calls == [] and not any(c[0] == "merge" for c in port.calls)


def test_an_unavailable_advisor_neither_blocks_nor_authorizes(tmp_path: Path) -> None:
    def broken(_f: dict, _v: pm.Verdict) -> dict:
        raise RuntimeError("model offline")

    (row,) = run(tmp_path, FakePort(), advisor=broken)
    assert row["merged"] is True  # the deterministic gates alone decided


# ---- the real GitHub port: commands, exact-head merge, no branch deletion --------------------------------------------


def test_the_github_port_merges_by_squash_on_the_verified_head_and_never_deletes_a_branch(monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setattr(pm.subprocess, "run", fake_run)
    port = pm.GhPort()
    port.merge(REPO, 7, HEAD)
    port.mark_ready(REPO, 7)
    merge, ready = seen
    assert merge[:3] == ["gh", "pr", "merge"] and "--squash" in merge and f"{HEAD}" in merge
    assert merge[merge.index("--match-head-commit") + 1] == HEAD
    assert "--delete-branch" not in merge and "--admin" not in merge and "--auto" not in merge
    assert ready[:3] == ["gh", "pr", "ready"]


def test_the_github_port_fails_closed_on_incomplete_review_evidence(monkeypatch) -> None:
    pages = {"data": {"repository": {"pullRequest": {"reviewThreads": {"pageInfo": {"hasNextPage": True}, "nodes": []}}}}}
    monkeypatch.setattr(pm.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, json.dumps(pages), ""))
    with pytest.raises(RuntimeError, match="REVIEW_THREADS_INCOMPLETE"):
        pm.GhPort().threads(REPO, 7)
    monkeypatch.setattr(pm.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", "HTTP 404: Not Found"))
    assert pm.GhPort().file_text(REPO, "engineering/capability-graph.json", "main") is None
    with pytest.raises(RuntimeError, match="GH_FAILED"):
        pm.GhPort().main_sha(REPO)


def test_capability_level_evidence_is_append_only_too() -> None:
    base = graph([slice_row(state="VERIFIED_COMPLETE", verification=["pytest -q"])])
    base["capabilities"][0].update(state="VERIFIED_COMPLETE", verification=["pytest -q"])
    head = copy.deepcopy(base)
    head["capabilities"][0]["verification"] = ["different"]
    assert "EVIDENCE_REWRITTEN:cap-cache.verification" in pm.graph_change_problems(base, head)
    head["capabilities"][0]["verification"] = ["pytest -q", "ci run 123"]
    assert pm.graph_change_problems(base, head) == []


# ---- wiring: every runner uses this merger, and live runs honour owner control -------------------------------------------

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("script", ["independent_pr_controller.py", "green_pr_watch_loop.py"])
def test_the_runners_use_the_portfolio_merger_and_not_the_looser_legacy_cycle(script: str) -> None:
    text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
    assert "portfolio_merger.cycle(" in text and "green_pr_merger" not in text


def load_cli():
    import importlib.util

    spec = importlib.util.spec_from_file_location("portfolio_merger_cli", ROOT / "scripts" / "iron_god_portfolio_merger.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_live_cli_pass_requires_owner_authority_and_a_dry_run_never_acts(tmp_path: Path, monkeypatch) -> None:
    cli = load_cli()
    monkeypatch.setattr(cli.portfolio_merger, "cycle", lambda *a, **k: pytest.fail("must not run without authority"))
    monkeypatch.setattr(cli, "owner_control_status", lambda _root: "OWNER_CONTROL_ACTIVE")
    assert cli.run(tmp_path, dry_run=False) == (2, {"status": "OWNER_CONTROL_ACTIVE", "acted": False})
    assert cli.run(None, dry_run=False)[1]["status"] == "STATE_ROOT_REQUIRED"
    seen: list[dict] = []
    monkeypatch.setattr(cli.portfolio_merger, "cycle", lambda root, dry_run=False, **k: seen.append({"dry": dry_run}) or [])
    code, payload = cli.run(None, dry_run=True)
    assert code == 0 and payload["status"] == "DRY_RUN" and payload["acted"] is False and seen == [{"dry": True}]
    monkeypatch.setattr(cli, "owner_control_status", lambda _root: "AUTHORIZED")
    monkeypatch.setattr(cli.portfolio_merger, "cycle", lambda *a, **k: [{"decision": "MERGE_FAILED"}])
    assert cli.run(tmp_path, dry_run=False)[0] == 1


def test_a_new_slice_inherits_the_fences_the_graph_already_declares_not_engine_wide_defaults() -> None:
    fences = ["engineering/**", ".github/**", "pyproject.toml"]
    base = graph([slice_row(forbidden_paths=fences)])
    inheriting = graph([slice_row(forbidden_paths=fences), slice_row("s2", forbidden_paths=[*fences, "docs/x/**"])])
    assert pm.graph_change_problems(base, inheriting) == []
    dropping = graph([slice_row(forbidden_paths=fences), slice_row("s2", forbidden_paths=fences[:1])])
    assert any(p.startswith("NEW_SLICE_FORBIDDEN_NOT_INHERITED") for p in pm.graph_change_problems(base, dropping))


# ---- review findings: renames, owner veto per mutation, capability dependencies ---------------------------------------


def rename(old: str, new: str) -> dict:
    return {**file_row(new, patch=None, status="renamed", add=0, dele=0), "previous_filename": old}


@pytest.mark.parametrize("old, new, fragment", [
    (".github/workflows/ci.yml", "pkg/config.txt", "SECURITY_SENSITIVE_PATH:.github/workflows/ci.yml"),
    ("docs/outside.md", "pkg/inside.md", "OUT_OF_SCOPE_PATHS:docs/outside.md"),
    ("pkg/pyproject.toml", "pkg/renamed.txt", "DEPENDENCY_CHANGE_NEEDS_OWNER:pkg/pyproject.toml"),
    ("tests/test_cache.py", "pkg/cache_notes.py", "TEST_FILE_DELETED:tests/test_cache.py"),
])
def test_a_rename_is_authorized_and_checked_on_both_its_old_and_new_path(old: str, new: str, fragment: str) -> None:
    result = verdict(files=[file_row("pkg/cache.py"), rename(old, new)])
    assert result.action == pm.ESCALATE and any(fragment in r for r in result.reasons), result


def test_a_rename_inside_the_scope_is_fine_and_changes_the_diff_digest() -> None:
    inside = rename("pkg/old.py", "pkg/new.py")
    assert verdict(files=[inside]).action == pm.MERGE
    assert pm.diff_digest([inside]) != pm.diff_digest([{**inside, "previous_filename": "pkg/other.py"}])


def test_the_github_port_keeps_the_previous_filename_of_a_rename(monkeypatch) -> None:
    page = [{"filename": "pkg/config.txt", "previous_filename": ".github/workflows/ci.yml", "status": "renamed",
             "additions": 0, "deletions": 0}]
    monkeypatch.setattr(pm.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, json.dumps([page]), ""))
    (row,) = pm.GhPort().files(REPO, 7)
    assert row["previous_filename"] == ".github/workflows/ci.yml" and row["path"] == "pkg/config.txt"


def test_owner_control_taken_back_mid_pass_stops_every_later_mutation(tmp_path: Path) -> None:
    port = FakePort()
    port.pulls[REPO] = [FakePort.summary(isDraft=True), FakePort.summary(number=8, isDraft=True)]
    seen: list[str] = []

    def guard() -> str | None:
        seen.append("call")
        return None if len(seen) <= 3 else "OWNER_CONTROL_ACTIVE"  # revoked after the first pull request was handled

    rows = pm.cycle(tmp_path, repositories=(REPO,), port=port, clock=lambda: NOW, guard=guard)
    assert sum(1 for c in port.calls if c[0] == "ready") == 1
    assert rows[-1]["decision"] == "OWNER_CONTROL_ACTIVE" and rows[-1]["merged"] is False


def test_owner_control_taken_back_during_the_reread_prevents_the_merge(tmp_path: Path) -> None:
    port = FakePort()
    calls = {"n": 0}

    def guard() -> str | None:
        calls["n"] += 1
        return None if calls["n"] <= 2 else "OWNER_CONTROL_ACTIVE"  # allowed before the pass and before the re-read

    (row,) = pm.cycle(tmp_path, repositories=(REPO,), port=port, clock=lambda: NOW, guard=guard)
    assert row["decision"] == "OWNER_CONTROL_ACTIVE" and not any(c[0] == "merge" for c in port.calls)


def test_changing_an_existing_capability_prerequisites_is_not_a_purely_additive_graph_edit() -> None:
    base = graph()
    head = copy.deepcopy(base)
    head["capabilities"][0]["depends_on"] = ["something-else"]
    assert "CAPABILITY_DEPENDENCIES_EDITED:cap-cache" in pm.graph_change_problems(base, head)
    assert planning(head).action == pm.REFUSE


# ---- the engine's own roadmap-progress records ----------------------------------------------------------------------------

PROGRESS_TITLE = "Iron God roadmap progress: record 1 merged slice(s)"
CITED = "https://github.com/jweter/knowledge-engine-core/pull/12"


def delivered(evidence: str = f"{CITED} merged (capable-builder pull request)", **over: Any) -> dict:
    head = graph([slice_row(state="VERIFIED_COMPLETE", verification=[evidence], **over)])
    return head


def with_extra_capability() -> dict:
    head = delivered()
    extra = copy.deepcopy(graph()["capabilities"][0])
    extra["id"] = "extra"
    head["capabilities"].append(extra)
    head["roadmap"] = ["cap-cache", "extra"]
    return head


def progress(head_graph: dict, evidence: dict | None = None, files: list | None = None, **over: Any) -> pm.Verdict:
    rows = files if files is not None else [file_row(pm.GRAPH_PATH, patch="+graph\n")]
    states = {CITED: "MERGED"} if evidence is None else evidence
    return verdict(title=PROGRESS_TITLE, files=rows, graph_head=json.dumps(head_graph), progress_evidence=states, **over)


def test_a_roadmap_progress_record_merges_when_every_cited_pull_request_really_merged() -> None:
    result = progress(delivered())
    assert result.action == pm.MERGE and result.authorization["kind"] == "progress"
    assert result.authorization["evidence"] == {CITED: "MERGED"}
    assert verdict(title=PROGRESS_TITLE, draft=True, files=[file_row(pm.GRAPH_PATH)],
                   graph_head=json.dumps(delivered()), progress_evidence={CITED: "MERGED"}).action == pm.PROMOTE


@pytest.mark.parametrize("evidence, fragment", [
    ({CITED: "OPEN"}, f"PROGRESS_EVIDENCE_NOT_MERGED:{CITED}"),
    ({CITED: "CLOSED"}, "PROGRESS_EVIDENCE_NOT_MERGED"),
    ({}, "PROGRESS_EVIDENCE_NOT_MERGED"),
    ({CITED: "NOT_AN_AUTHORIZED_REPOSITORY"}, "PROGRESS_EVIDENCE_NOT_MERGED"),
])
def test_completion_evidence_must_be_confirmed_merged_by_github_never_taken_on_trust(evidence: dict, fragment: str) -> None:
    result = progress(delivered(), evidence=evidence)
    assert result.action == pm.REFUSE and any(fragment in r for r in result.reasons), result


@pytest.mark.parametrize("make_head, fragment", [
    (lambda: delivered(evidence="verified by hand, trust me"), "PROGRESS_WITHOUT_EVIDENCE:cap-cache/s1"),
    (lambda: graph(), "PROGRESS_COMPLETES_NOTHING"),
    (lambda: graph([slice_row(state="VERIFIED_COMPLETE", verification=[f"{CITED} merged"]), slice_row("s2")]),
     "PROGRESS_ADDS_WORK:cap-cache/s2"),
    (lambda: delivered(allowed_paths=["**"]), "SLICE_AUTHORITY_EDITED:cap-cache/s1.allowed_paths"),
    (lambda: with_extra_capability(), "PROGRESS_ADDS_CAPABILITY:extra"),
])
def test_a_progress_record_cannot_add_work_rewrite_authority_or_invent_evidence(make_head, fragment: str) -> None:
    result = progress(make_head())
    assert result.action == pm.REFUSE and any(fragment in r for r in result.reasons), result


def test_a_progress_record_that_touches_anything_but_the_graph_or_comes_from_another_account_is_escalated() -> None:
    result = progress(delivered(), files=[file_row(pm.GRAPH_PATH), file_row("pkg/cache.py")])
    assert result.action == pm.ESCALATE and any("OUT_OF_SCOPE_PATHS:pkg/cache.py" in r for r in result.reasons)
    assert progress(delivered(), author="someone-else").action == pm.ESCALATE
    assert verdict(title="Iron God roadmap progress", author="dependabot[bot]").action == pm.ESCALATE


def test_the_cycle_confirms_each_cited_pull_request_with_github_and_merges_the_record(tmp_path: Path) -> None:
    port = FakePort(files_rows=[file_row(pm.GRAPH_PATH, patch="+graph\n")], graph_head_text=json.dumps(delivered()))
    port.pulls[REPO] = [FakePort.summary(title=PROGRESS_TITLE)]
    (row,) = run(tmp_path, port)
    assert row["merged"] is True and ("pull_state", REPO, 12) in port.calls
    entry = ledger(tmp_path)[0]
    assert entry["action"] == "MERGED" and entry["authorization"]["evidence"] == {CITED: "MERGED"}
    port = FakePort(files_rows=[file_row(pm.GRAPH_PATH)], graph_head_text=json.dumps(delivered()),
                    pull_states={(REPO, 12): "OPEN"})
    port.pulls[REPO] = [FakePort.summary(title=PROGRESS_TITLE)]
    (row,) = run(tmp_path / "unmerged", port)
    assert row["decision"] == pm.REFUSE and not row["merged"] and not any(c[0] == "merge" for c in port.calls)


def test_evidence_naming_a_repository_outside_the_portfolio_is_never_confirmed() -> None:
    foreign = "https://github.com/someone/else/pull/9"
    head = delivered(evidence=f"{foreign} merged")
    urls = pm.new_evidence_urls(graph(), head)
    assert urls == {("cap-cache", "s1"): [foreign]}
    port = FakePort(files_rows=[file_row(pm.GRAPH_PATH)], graph_head_text=json.dumps(head))
    port.pulls[REPO] = [FakePort.summary(title=PROGRESS_TITLE)]
    facts_row = pm.gather(port, REPO, port.pulls[REPO][0])
    assert facts_row["progress_evidence"] == {foreign: "NOT_AN_AUTHORIZED_REPOSITORY"}
    assert not any(c[0] == "pull_state" for c in port.calls)


def test_the_github_port_reads_the_state_of_a_cited_pull_request(monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"state": "MERGED"}), "")

    monkeypatch.setattr(pm.subprocess, "run", fake_run)
    assert pm.GhPort().pull_state(REPO, 12) == "MERGED"
    assert seen[0][:3] == ["gh", "pr", "view"] and "--repo" in seen[0]


def test_a_progress_record_cannot_complete_a_capability_or_touch_anything_but_slice_state_and_evidence() -> None:
    completes_capability = delivered()
    completes_capability["capabilities"][0].update(state="VERIFIED_COMPLETE", verification=["pytest -q"])
    result = progress(completes_capability)
    assert result.action == pm.REFUSE and "PROGRESS_CHANGES_CAPABILITY:cap-cache" in result.reasons, result
    assert pm.progress_problems(graph(), delivered(), {CITED: "MERGED"}) == []  # the legitimate shape is accepted
    rewrites_text = delivered()
    rewrites_text["capabilities"][0]["capability"] = "Something else entirely"
    assert "PROGRESS_CHANGES_CAPABILITY:cap-cache" in progress(rewrites_text).reasons
    reorders = delivered()
    reorders["roadmap"] = []  # an invalid graph is refused before the progress rule, but it is still refused
    assert progress(reorders).action == pm.REFUSE
    extra_key = delivered()
    extra_key["capabilities"][0]["slices"][0]["notes"] = "smuggled field"
    assert any(r.startswith("PROGRESS_CHANGES_MORE_THAN") for r in progress(extra_key).reasons)


# ---- owner-approved, head-pinned scope exceptions ---------------------------------------------------------------------


def grant(**over: Any) -> dict:
    base = {"repository": REPO, "pr": 7, "head": HEAD, "paths": ["docs/other.md"], "approved_by": "jweter",
            "approved_at": "2026-11-07T02:00:00+00:00", "reason": "owner approved this exact move"}
    base.update(over)
    return base


def excepted(exceptions: list, **over: Any) -> pm.Verdict:
    f = pm._apply_exceptions(facts(files=[file_row("pkg/cache.py"), file_row("docs/other.md")], **over), exceptions)
    return pm.evaluate(f, now=NOW)


def test_an_owner_exception_clears_exactly_its_named_path_for_that_head_and_is_recorded() -> None:
    assert verdict(files=[file_row("pkg/cache.py"), file_row("docs/other.md")]).action == pm.ESCALATE
    result = excepted([grant()])
    assert result.action == pm.MERGE and result.authorization["owner_exception"][0]["approved_by"] == "jweter"


def test_an_exception_does_not_cover_other_paths_other_heads_or_other_pull_requests() -> None:
    f = [file_row("pkg/cache.py"), file_row("docs/other.md"), file_row("docs/more.md")]
    result = pm.evaluate(pm._apply_exceptions(facts(files=f), [grant()]), now=NOW)
    assert result.action == pm.ESCALATE and any("docs/more.md" in r and "docs/other.md" not in r for r in result.reasons)
    assert excepted([grant(head="b" * 40)]).action == pm.ESCALATE      # the head moved: the exception no longer applies
    assert excepted([grant(pr=8)]).action == pm.ESCALATE


def test_an_exception_never_lifts_protected_paths_or_a_failing_gate() -> None:
    f = [file_row("pkg/cache.py"), file_row(".github/workflows/ci.yml")]
    protected = pm.evaluate(pm._apply_exceptions(facts(files=f), [grant(paths=[".github/workflows/ci.yml"])]), now=NOW)
    assert protected.action == pm.ESCALATE
    assert excepted([grant()], checks=checks(conclusion="FAILURE")).action == pm.REFUSE


def test_only_complete_owner_signed_exceptions_load(tmp_path: Path) -> None:
    directory = tmp_path / "portfolio-merger"
    directory.mkdir()
    (directory / pm.EXCEPTIONS_FILE).write_text(json.dumps([grant(), grant(approved_by="someone"), grant(head="short"),
                                                            {"repository": REPO}]), encoding="utf-8")
    assert pm.load_exceptions(tmp_path) == [grant()]
    (directory / pm.EXCEPTIONS_FILE).write_text("{not json", encoding="utf-8")
    assert pm.load_exceptions(tmp_path) == []
