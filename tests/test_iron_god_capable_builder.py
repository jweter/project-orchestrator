"""Capable-builder hand-off: bounded, off by default, and never a way to bypass the repository's own gates."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from project_orchestrator import iron_god_capable_builder as cb
from project_orchestrator.claude_dispatch import ROUTINES, specialist_scorecard

TOKEN = ROUTINES[cb.ROUTINE][1]
NOW = time.time()  # claude_dispatch stamps started_at with the real clock; every test time is relative to it


def request(ident: str = "RM-demo-1", repository: str = "jweter/VoiceStudio", builder: str = "CAPABLE") -> dict:
    return {"id": ident, "repository": repository, "exact_head": "a" * 40,
            "roadmap_ref": "engineering/capability-graph.json#cap/slice", "objective": "Add the thing.",
            "slice_acceptance": ["it works", "tests cover it"], "allowed_paths": ["backend/**", "tests/**"],
            "forbidden_paths": [".github/**", "engineering/**"], "prior_failures": [], "development_rules": [],
            "route": {"builder": builder, "reason": "SLICE_EXCEEDS_CALIBRATED_CAPABILITY"}}


class Opener:
    """A stand-in for the routine API: records every fired request and answers with a session."""

    def __init__(self) -> None:
        self.fired: list[dict] = []

    def __call__(self, req, timeout=None):
        self.fired.append({"url": req.full_url, "auth": req.headers.get("Authorization"), "body": json.loads(req.data)})
        number = len(self.fired)
        body = json.dumps({"claude_code_session_id": f"s{number}", "claude_code_session_url": f"https://claude/s{number}"})

        class Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_a):
                return False

            def read(self_inner):
                return body.encode()

        return Response()


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    global NOW
    NOW = time.time()  # restart the baseline per test so earlier tests' runtime cannot skew the age arithmetic
    monkeypatch.setenv(TOKEN, "test-token-not-real")


def go(tmp_path: Path, opener: Opener, req: dict | None = None, **kw) -> dict:
    kw.setdefault("enabled", True)
    kw.setdefault("now", NOW)
    return cb.dispatch_request(req or request(), lane_root=tmp_path, opener=opener, **kw)


# --- decisions -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("builder", ["LOCAL", "NONE"])
def test_only_capable_routed_requests_are_ever_dispatched(tmp_path: Path, configured: None, builder: str) -> None:
    opener = Opener()

    result = go(tmp_path, opener, request(builder=builder))

    assert result == {"dispatched": False, "dispatch": False, "reason": "NOT_ROUTED_TO_CAPABLE_BUILDER"}
    assert opener.fired == [] and not cb.dispatch_state(tmp_path).exists()


def test_dispatch_is_off_by_default_and_fires_nothing(tmp_path: Path, configured: None) -> None:
    opener = Opener()

    result = go(tmp_path, opener, enabled=False)

    assert result["reason"] == "CAPABLE_BUILDER_DISABLED" and opener.fired == []


def test_a_missing_token_records_a_blocker_reason_and_fires_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN, raising=False)
    opener = Opener()

    result = go(tmp_path, opener)

    assert result["reason"] == "CAPABLE_BUILDER_UNCONFIGURED" and result["needs"] == TOKEN and opener.fired == []


def test_a_configured_enabled_request_fires_one_routine_with_a_self_contained_prompt(tmp_path: Path, configured: None) -> None:
    opener = Opener()

    result = go(tmp_path, opener)

    assert result["dispatched"] is True and result["task_key"] == "RM-demo-1#a0"
    (fired,) = opener.fired
    assert fired["auth"] == "Bearer test-token-not-real"
    prompt = fired["body"]["text"]
    for needed in ("RM-demo-1", "engineering/capability-graph.json#cap/slice", "Add the thing.", "it works",
                   "backend/**", "Forbidden paths (never touch): .github/**, engineering/**",
                   "open ONE pull request whose title starts with 'Iron God [RM-demo-1]'", "Do not merge",
                   "canonical preflight"):
        assert needed in prompt
    assert "test-token-not-real" not in prompt  # the credential never travels in the task text


def test_one_open_dispatch_per_repository_and_a_daily_budget(tmp_path: Path, configured: None) -> None:
    opener = Opener()
    assert go(tmp_path, opener, request("RM-a", "jweter/A"))["dispatched"]

    busy = go(tmp_path, opener, request("RM-b", "jweter/A"))
    other = go(tmp_path, opener, request("RM-c", "jweter/B"))
    capped = go(tmp_path, opener, request("RM-d", "jweter/C"))

    assert busy["reason"] == "REPOSITORY_HAS_OPEN_DISPATCH"
    assert other["dispatched"] is True  # other repositories are not blocked by one busy repository
    assert capped["reason"] == "DAILY_BUDGET_EXHAUSTED" and len(opener.fired) == 2
    later = go(tmp_path, opener, request("RM-d", "jweter/C"), now=NOW + 25 * 3600)
    assert later["dispatched"] is True  # the budget is a rolling day, not a permanent lock


def test_a_failed_request_gets_a_bounded_number_of_attempts(tmp_path: Path, configured: None) -> None:
    opener = Opener()
    moment = NOW
    for attempt in range(cb.MAX_ATTEMPTS):
        assert go(tmp_path, opener, now=moment, daily_limit=10)["task_key"] == f"RM-demo-1#a{attempt}"
        moment += cb.STALE_SECONDS + 1
        cb.reconcile(tmp_path, now=moment, lookup=lambda _repo, _id: None)  # no pull request ever appeared

    result = go(tmp_path, opener, now=moment, daily_limit=10)

    assert result["reason"] == "ATTEMPTS_EXHAUSTED" and len(opener.fired) == cb.MAX_ATTEMPTS


# --- completion is observed, not claimed -----------------------------------------------------------------------


def test_an_observed_pull_request_closes_the_dispatch_and_feeds_the_scorecard(tmp_path: Path, configured: None) -> None:
    opener = Opener()
    go(tmp_path, opener)
    assert cb.open_request_ids(tmp_path, now=NOW + 60) == {"RM-demo-1"}

    changed = cb.reconcile(tmp_path, now=NOW + 120, lookup=lambda repo, ident: f"https://github.com/{repo}/pull/9")

    assert changed[0]["outcome"] == "PRODUCT_PR" and changed[0]["pr_url"].endswith("/pull/9")
    assert cb.open_request_ids(tmp_path, now=NOW + 130) == set()
    score = specialist_scorecard(cb.dispatch_state(tmp_path))[cb.ROUTINE]
    assert score["completed"] == 1 and score["advancement_rate"] == 1.0 and score["role_adherence_rate"] == 1.0


def test_a_hung_session_is_failed_by_age_not_waited_on_forever(tmp_path: Path, configured: None) -> None:
    opener = Opener()
    go(tmp_path, opener)

    assert cb.reconcile(tmp_path, now=NOW + 3600, lookup=lambda _r, _i: None) == []
    changed = cb.reconcile(tmp_path, now=NOW + cb.STALE_SECONDS + 1, lookup=lambda _r, _i: None)

    assert changed[0]["outcome"] == "FAIL"
    assert specialist_scorecard(cb.dispatch_state(tmp_path))[cb.ROUTINE]["completed"] == 1


def test_a_stale_dispatch_no_longer_counts_as_pending(tmp_path: Path, configured: None) -> None:
    go(tmp_path, Opener())

    assert cb.open_request_ids(tmp_path, now=NOW + cb.STALE_SECONDS + 1) == set()


def test_pull_request_lookup_only_accepts_a_title_that_names_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    class Done:
        returncode = 0
        stdout = json.dumps([{"url": "https://x/pull/1", "title": "Something else mentioning Iron God [RM-demo-1]"},
                             {"url": "https://x/pull/2", "title": "Iron God [RM-demo-1] add the thing"}])

    monkeypatch.setattr(cb.subprocess, "run", lambda *a, **k: Done())

    assert cb.find_pull_request("jweter/VoiceStudio", "RM-demo-1") == "https://x/pull/2"


def test_a_failing_lookup_is_not_a_pull_request(monkeypatch: pytest.MonkeyPatch) -> None:
    class Failed:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(cb.subprocess, "run", lambda *a, **k: Failed())

    assert cb.find_pull_request("jweter/VoiceStudio", "RM-demo-1") is None


def test_the_adapter_never_touches_the_owner_controls_or_merges() -> None:
    source = Path(cb.__file__).read_text(encoding="utf-8")
    for forbidden in ("gh pr merge", "Enable-ScheduledTask", "git push", "owner_control", "branch -D"):
        assert forbidden not in source
    assert time.time() > 0
