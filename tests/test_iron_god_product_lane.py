"""Portfolio rotation: round-robin product turns, PR -> CI green -> merge, shared working memory."""

from __future__ import annotations

import importlib.util
import json
import os
import random
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_product_lane as pl
from project_orchestrator import iron_god_working_memory as wm
from project_orchestrator import supervisor_runtime
from project_orchestrator.runtime_leases import DEFAULT_RUNTIME_CAPACITIES, RuntimeLeasePool
from project_orchestrator.shared_memory import load_shared_memory

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows engine")

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "engineering" / "iron-god-commissioning.json").read_text(encoding="utf-8"))
SPEC = importlib.util.spec_from_file_location("product_worker_under_test", ROOT / "scripts" / "iron_god_product_worker.py")
WORKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER)
# Real portfolio product repositories (Canon checks project membership); local bare repos stand in for GitHub.
REPOS = ["jweter/Project-Everward", "jweter/VoiceStudio", "jweter/rocksmith-cdlc-generator"]
PERMIT = SimpleNamespace(permitted=True, reasons=())
DENY = SimpleNamespace(permitted=False, reasons=("OWNER_CONTROL_ACTIVE",))


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def remote(tmp: Path, name: str) -> Path:
    """A bare 'GitHub' repository with a main branch shaped like a portfolio product."""
    src = tmp / "seed" / name
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (src / "pkg" / "core.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (src / "tests").mkdir()
    (src / "tests" / "test_core.py").write_text("from pkg.core import one\n\n\ndef test_one():\n    assert one() == 1\n",
                                                encoding="utf-8")
    (src / "engineering").mkdir()
    (src / "engineering" / "preflight.py").write_text("print('ok')\n", encoding="utf-8")
    (src / "AGENTS.md").write_text(f"# {name}\nRoadmap: add small helpers.\n", encoding="utf-8")
    git(tmp, "init", "-q", "-b", "main", str(src))
    git(src, "config", "user.email", "o@example.com")
    git(src, "config", "user.name", "Owner")
    git(src, "add", ".")
    git(src, "commit", "-q", "-m", "product")
    bare = tmp / "github" / f"{name}.git"
    git(tmp, "clone", "-q", "--bare", str(src), str(bare))
    return bare


class FakeGitHub:
    def __init__(self, buckets=("pass",)):
        self.buckets = list(buckets)
        self.pushed, self.opened, self.merged = [], [], []

    def push(self, clone, branch, head):
        git(clone, "push", "-q", "origin", f"{head}:refs/heads/{branch}")
        self.pushed.append((branch, head))

    def open_pr(self, repo, branch, title, body):
        self.opened.append({"repo": repo, "branch": branch, "title": title, "body": body})
        number = len(self.opened)
        return {"url": f"https://github.com/{repo}/pull/{number}", "number": number}

    def checks(self, repo, number):
        return [{"name": f"ci-{i}", "state": b.upper(), "bucket": b} for i, b in enumerate(self.buckets)]

    def merge(self, repo, number, head):
        self.merged.append((repo, number, head))
        return "f" * 40


@pytest.fixture
def env(tmp_path):
    release = tmp_path / "release"
    (release / "engineering").mkdir(parents=True)
    # Real product project ids: Canon only accepts handbacks for portfolio product projects.
    ids = dict(zip(REPOS, ("everward", "voicestudio", "rocksmith")))
    projects = [{"id": ids.get(r, "iron-god"), "repositories": [{"name": r}]}
                for r in [*REPOS, "jweter/project-orchestrator"]]
    (release / "engineering" / "portfolio.json").write_text(json.dumps({"projects": projects}), encoding="utf-8")
    remotes = {r: remote(tmp_path, r.split("/")[1]) for r in REPOS}
    state = tmp_path / "ExponentialEngine" / "flow-state"
    pool = RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES, tmp_path / "runtime-leases.json")

    def sync(lane, repo):
        clone = lane.clone(repo)
        if not (clone / ".git").exists():
            clone.parent.mkdir(parents=True, exist_ok=True)
            git(tmp_path, "clone", "-q", str(remotes[repo]), str(clone))
        git(clone, "fetch", "-q", "--prune", "origin", "main")
        return clone, git(clone, "rev-parse", "origin/main")

    return SimpleNamespace(release=release, state=state, pool=pool, sync=sync, remotes=remotes, tmp=tmp_path,
                           memory=tmp_path / "shared-memory", policy=POLICY)


GOOD_PLAN = {
    "function_name": "add_two", "signature": "def add_two(value: int) -> int:",
    "module_path": "pkg/extra.py", "objective": "Return the given integer plus two.",
    "examples": [{"call": "add_two(1)", "why": "1 + 2", "expected": 3},
                 {"call": "add_two(0)", "why": "0 + 2", "expected": 2},
                 {"call": "add_two(-2)", "why": "-2 + 2", "expected": 0}],
    "note": "Added pkg.extra.add_two; next: a subtract helper.",
}
GOOD_MODULE = 'def add_two(value: int) -> int:\n    """Return value plus two."""\n    return value + 2\n'


def good_plan(worktree, packet, check, feedback=""):
    check()
    return dict(GOOD_PLAN)


def good_agent(worktree, message, files, check, source=GOOD_MODULE):
    check()
    assert files == ["pkg/extra.py"]  # the build prompt or the one repair prompt: only the module, never the tests
    assert "Create or edit ONLY these files: pkg/extra.py" in message or "Fix only pkg/extra.py" in message
    assert (worktree / "tests" / "test_extra.py").is_file()  # the worker wrote the tests before the build
    (worktree / "pkg" / "extra.py").write_text(source, encoding="utf-8")


class Runner:
    def __init__(self, agent=good_agent, preflight_ok=True, gate=None, plan=good_plan,
                 rederive=lambda packet, plan, call: WORKER.NO_OPINION):
        self.agent, self.preflight_ok, self.gate, self.plan = agent, preflight_ok, gate, plan
        self.rederive = rederive
        self.spawned = 0

    def spawn(self, release_repo, packet_path, handback_path, *, release_sha, log_path):
        self.spawned += 1
        packet = json.loads(Path(packet_path).read_text(encoding="utf-8"))
        handback = WORKER.execute_product_packet(
            packet, authority=lambda: True, plan=self.plan, run_agent=self.agent, gates=lambda *_a: self.gate,
            rederive=self.rederive, format_files=lambda *_a: "none",
            preflight=lambda *_a: {"command": "python engineering/preflight.py", "returncode": 0 if self.preflight_ok
                                   else 1, "ok": self.preflight_ok, "output_tail": ""})
        WORKER._write(Path(handback_path), handback)
        return {"pid": 1, "process_start_fingerprint": "windows:1", "host": "fake"}


def step(env, runner, github=None, *, decision=PERMIT, allow=True, owned=lambda repo: [], seed=7, **kw):
    return pl.advance_product_lane(
        env.state, env.release, supervisor={}, release_sha="a" * 40, allow_dispatch=allow,
        decide=lambda: decision, spawn=runner.spawn, alive=lambda _w: False, stop=lambda _w: None,
        resources=lambda: {"admitted": True}, lease_pool=env.pool, capability=lambda _s: None,
        github=github or FakeGitHub(), sync=env.sync, owned_paths=owned, gates=lambda *_a: None,
        memory_root=env.memory, rng=random.Random(seed), policy=env.policy, **kw)


def test_rotation_picks_randomly_first_then_round_robin_and_never_starves_a_repo(env):
    lane = pl.ProductLane(env.state)
    first = pl.next_repository(lane, REPOS, set(), random.Random(1))
    order = lane.rotation()["order"]
    assert sorted(order) == REPOS and first in REPOS
    picks = []
    for _ in range(6):
        repo = pl.next_repository(lane, REPOS, set(), random.Random(99))
        pl._advance_cursor(lane, repo, "p")
        picks.append(repo)
    assert picks[:3] == [order[(order.index(picks[0]) + i) % 3] for i in range(3)]
    assert picks[3:] == picks[:3]  # strict rotation: every repository gets every third turn
    busy = {picks[0]}
    assert pl.next_repository(lane, REPOS, busy, random.Random(5)) != picks[0]


def test_rotation_keeps_its_order_when_repositories_are_added_or_removed(env):
    lane = pl.ProductLane(env.state)
    pl._advance_cursor(lane, pl.next_repository(lane, REPOS, set(), random.Random(3)), "p")
    before = lane.rotation()["order"]
    pl.next_repository(lane, [*REPOS, "jweter/knowledge-engine-ai"], set(), random.Random(3))
    after = lane.rotation()["order"]
    assert [r for r in after if r != "jweter/knowledge-engine-ai"] == before and "jweter/knowledge-engine-ai" in after
    pl.next_repository(lane, REPOS[:2], set(), random.Random(3))
    assert set(lane.rotation()["order"]) == set(REPOS[:2])


def test_full_turn_publishes_by_pull_request_and_merges_only_after_ci_is_green(env):
    runner, github = Runner(), FakeGitHub(("pass", "pass", "skipping"))
    dispatched = step(env, runner, github)
    assert dispatched["status"] == "DISPATCHED" and dispatched["scope"] == ["pkg/", "tests/"]
    repo = dispatched["repository"]
    assert dispatched["base_sha"] == git(env.remotes[repo], "rev-parse", "main")  # GitHub's current main
    accepted = step(env, runner, github)
    assert accepted["status"] == "ACCEPTED_LOCAL", accepted
    assert github.pushed == [] and github.merged == []  # nothing outward before the next authorized pass
    step(env, runner, github, allow=False)  # publish: push + PR
    assert len(github.opened) == 1 and github.merged == []
    assert "add_two" in github.opened[0]["title"] and "tests/test_extra.py" in github.opened[0]["body"]
    pushed_branch, head = github.pushed[0]
    assert git(env.remotes[repo], "rev-parse", f"refs/heads/{pushed_branch}") == head
    step(env, runner, github, allow=False)  # CI green -> squash merge
    assert github.merged == [(repo, 1, head)]
    items = pl.ProductLane(env.state).publications()["items"]
    assert [i["state"] for i in items.values()] == ["MERGED"]
    lessons = load_shared_memory(env.memory)["entries"]
    assert len(lessons) == 1 and lessons[0]["evidence_state"] == "VERIFIED" and lessons[0]["repository"] == repo
    board = wm.load(env.state)
    assert board["repositories"][repo]["last_outcome"] == "MERGED" and board["active"] is None
    assert "Added pkg.extra.add_two" in board["repositories"][repo]["notes"][-1]["note"]


@pytest.mark.parametrize("buckets", [("pass", "fail"), ("cancel",)])
def test_failed_ci_blocks_the_turn_and_never_merges(env, buckets):
    runner, github = Runner(), FakeGitHub(buckets)
    step(env, runner, github)
    step(env, runner, github)
    step(env, runner, github, allow=False)
    step(env, runner, github, allow=False)
    item = next(iter(pl.ProductLane(env.state).publications()["items"].values()))
    assert item["state"] == "BLOCKED" and item["reason"] == "BLOCKED_CI_FAILED"
    assert github.merged == []
    assert wm.load(env.state)["repositories"][item["repository"]]["blockers"]


def test_ci_verdict_requires_reported_green_checks():
    assert pl.ci_verdict([], published_at=0, now=60) == "WAIT"
    assert pl.ci_verdict([], published_at=0, now=pl.CI_APPEAR_SECONDS + 1) == "BLOCKED_NO_CI_REPORTED"
    assert pl.ci_verdict([{"bucket": "pending"}], published_at=0, now=60) == "WAIT"
    assert pl.ci_verdict([{"bucket": "pending"}], published_at=0, now=pl.CI_TOTAL_SECONDS + 1) == "BLOCKED_CI_TIMEOUT"
    assert pl.ci_verdict([{"bucket": "skipping"}], published_at=0, now=60) == "BLOCKED_NO_CI_REPORTED"
    assert pl.ci_verdict([{"bucket": "pass"}, {"bucket": "fail"}], published_at=0, now=60) == "BLOCKED_CI_FAILED"
    assert pl.ci_verdict([{"bucket": "pass"}, {"bucket": "skipping"}], published_at=0, now=60) == "PASS"


def test_nothing_is_dispatched_pushed_or_merged_while_the_owner_holds_control(env):
    runner, github = Runner(), FakeGitHub()
    step(env, runner, github)
    step(env, runner, github)  # accepted, publication pending
    assert step(env, runner, github, decision=DENY)["status"] == "WAITING_FOR_OWNER_AUTHORITY"
    assert github.pushed == [] and github.opened == [] and runner.spawned == 1


def test_revocation_stops_the_active_product_worker(env):
    stopped = []
    pl.advance_product_lane(
        env.state, env.release, supervisor={}, release_sha="a" * 40, allow_dispatch=True, decide=lambda: PERMIT,
        spawn=lambda *a, **k: {"pid": 9, "process_start_fingerprint": "windows:9", "host": "x"},
        alive=lambda _w: True, stop=stopped.append, resources=lambda: {"admitted": True}, lease_pool=env.pool,
        capability=lambda _s: None, github=FakeGitHub(), sync=env.sync, owned_paths=lambda r: [],
        gates=lambda *_a: None, memory_root=env.memory, rng=random.Random(1), policy=env.policy)
    result = pl.advance_product_lane(
        env.state, env.release, supervisor={}, release_sha="a" * 40, allow_dispatch=True, decide=lambda: DENY,
        alive=lambda _w: True, stop=stopped.append, lease_pool=env.pool, policy=env.policy)
    assert result["status"] == "WORKER_STOPPED_AUTHORITY_REVOKED" and stopped[0]["pid"] == 9
    assert wm.load(env.state)["active"] is None


def test_stray_output_is_discarded_and_only_the_planned_files_are_committed(env):
    def sprawl(worktree, message, files, check):
        good_agent(worktree, message, files, check)
        (worktree / "AGENTS.md").write_text("rewritten", encoding="utf-8")
        (worktree / "First, we need to install the required libraries").write_text("junk", encoding="utf-8")
    runner = Runner(agent=sprawl)
    dispatched = step(env, runner)
    accepted = step(env, runner)
    assert accepted["status"] == "ACCEPTED_LOCAL", accepted
    clone = pl.ProductLane(env.state).clone(dispatched["repository"])
    files = git(clone, "diff", "--name-only", dispatched["base_sha"], accepted["head_sha"]).splitlines()
    assert sorted(files) == ["pkg/extra.py", "tests/test_extra.py"]


def test_a_plan_touching_a_file_under_review_is_refused_before_any_code(env):
    runner = Runner()
    step(env, runner, owned=lambda repo: ["pkg/extra.py"])
    rejected = step(env, runner)
    assert rejected["worker_status"] == "FAIL" and "PLAN_PATH_FORBIDDEN_OR_UNDER_REVIEW" in rejected["worker_summary"]


WRONG_MODULE = "def add_two(value):\n    return value\n"


def test_code_still_failing_its_generated_tests_after_one_repair_fails_fast(env):
    calls = []

    def stubborn(worktree, message, files, check):
        calls.append(message)
        if len(calls) == 1:
            good_agent(worktree, message, files, check, source=WRONG_MODULE)
        else:
            (worktree / "pkg" / "extra.py").write_text(WRONG_MODULE, encoding="utf-8")
    runner = Runner(agent=stubborn)
    step(env, runner)
    result = step(env, runner)
    assert result["worker_status"] == "FAIL" and result["worker_summary"] == "SLICE_TESTS_FAILED"
    assert len(calls) == 2 and "assert add_two(1) == 3" in calls[1]  # the repair saw the failing assertion


def test_one_repair_round_fixes_code_that_misses_its_examples(env):
    calls = []

    def learns(worktree, message, files, check):
        calls.append(files)
        source = WRONG_MODULE if len(calls) == 1 else GOOD_MODULE
        if len(calls) == 1:
            good_agent(worktree, message, files, check, source=source)
        else:
            (worktree / "pkg" / "extra.py").write_text(source, encoding="utf-8")
    runner = Runner(agent=learns)
    step(env, runner)
    assert step(env, runner)["status"] == "ACCEPTED_LOCAL"
    assert calls == [["pkg/extra.py"], ["pkg/extra.py"]]  # the repair may touch only the module


def test_a_repair_that_edits_the_generated_tests_gets_them_restored_and_still_has_to_pass(env):
    calls = []

    def cheats(worktree, message, files, check):
        calls.append(1)
        if len(calls) == 1:
            good_agent(worktree, message, files, check, source=WRONG_MODULE)
        else:
            (worktree / "tests" / "test_extra.py").write_text("def test_nothing():\n    pass\n", encoding="utf-8")
    runner = Runner(agent=cheats)
    step(env, runner)
    result = step(env, runner)
    assert result["worker_status"] == "FAIL" and result["worker_summary"] == "SLICE_TESTS_FAILED"


def test_stray_files_from_the_repair_round_are_discarded_too(env):
    calls = []

    def strays(worktree, message, files, check):
        calls.append(1)
        if len(calls) == 1:
            good_agent(worktree, message, files, check, source=WRONG_MODULE)
        else:
            (worktree / "pkg" / "extra.py").write_text(GOOD_MODULE, encoding="utf-8")
            (worktree / "notes.md").write_text("junk", encoding="utf-8")
    runner = Runner(agent=strays)
    step(env, runner)
    assert step(env, runner)["status"] == "ACCEPTED_LOCAL"


def test_non_stdlib_imports_are_refused(env):
    runner = Runner(agent=lambda w, m, f, c: good_agent(w, m, f, c, source="import numpy\n\n\n" + GOOD_MODULE))
    step(env, runner)
    result = step(env, runner)
    assert result["worker_status"] == "FAIL" and "STDLIB_GATE: NON_STDLIB_IMPORT: numpy" in result["worker_summary"]


def test_failing_preflight_or_change_gates_never_publish(env):
    github = FakeGitHub()
    for runner in (Runner(preflight_ok=False), Runner(gate="source_change_without_regression_test")):
        step(env, runner, github)
        result = step(env, runner, github)
        assert result["status"] != "ACCEPTED_LOCAL"
    step(env, Runner(), github, allow=False)
    assert github.pushed == []


def test_a_repository_awaiting_ci_is_skipped_so_its_next_turn_starts_from_the_merged_main(env):
    github = FakeGitHub(("pending",))
    runner = Runner()
    first = step(env, runner, github)["repository"]
    step(env, runner, github)  # accepted
    second = step(env, runner, github)  # publishes first; dispatches the next repository
    assert second["status"] == "DISPATCHED" and second["repository"] != first


def test_dispatch_waits_for_commissioning_and_for_calibration(env):
    runner = Runner()
    assert step(env, runner, allow=False)["status"] == "WAITING_FOR_COMMISSIONING_LANE"
    result = pl.advance_product_lane(
        env.state, env.release, supervisor={}, release_sha="a" * 40, allow_dispatch=True, decide=lambda: PERMIT,
        spawn=runner.spawn, alive=lambda _w: False, stop=lambda _w: None, resources=lambda: {"admitted": True},
        lease_pool=env.pool, capability=lambda _s: "CALIBRATION_REQUIRED", github=FakeGitHub(), sync=env.sync,
        owned_paths=lambda r: [], gates=lambda *_a: None, memory_root=env.memory, policy=env.policy)
    assert result["status"] == "WAITING_FOR_CALIBRATION" and runner.spawned == 0


def plan_for(env):
    runner = Runner()
    step(env, runner)
    packet = json.loads(next(pl.ProductLane(env.state).packets.glob("*.json")).read_text(encoding="utf-8"))
    return packet, Path(packet["worktree"])


def test_build_prompt_is_the_lean_commissioning_prompt_naming_only_the_planned_module(env):
    packet, worktree = plan_for(env)
    plan = WORKER.validate_plan(dict(GOOD_PLAN), worktree, packet["base_sha"], packet)
    message = WORKER.build_message(packet, plan)
    assert "Create or edit ONLY these files: pkg/extra.py" in message and "tests/" not in message.split("files:")[1]
    assert "add_two(1) returns 3" in message and len(message) < 2500


def test_the_worker_writes_the_tests_from_the_plans_exact_examples(env):
    packet, worktree = plan_for(env)
    plan = WORKER.validate_plan(dict(GOOD_PLAN), worktree, packet["base_sha"], packet)
    assert plan["test_path"] == "tests/test_extra.py" and plan["import_path"] == "pkg.extra"
    source = WORKER.render_tests(plan)
    assert source.startswith("from pkg.extra import add_two\n")
    assert "    assert add_two(-2) == 0\n" in source and source.count("def test_add_two_example_") == 3
    assert WORKER.import_path("src/rocksmith_cdlc_generator/tempo.py") == "rocksmith_cdlc_generator.tempo"
    flags = WORKER.render_tests({**plan, "examples": [{"call": "add_two(0)", "expected": "True"},
                                                      {"call": "add_two(1)", "expected": "None"}]})
    assert "assert add_two(0) is True\n" in flags and "assert add_two(1) is None\n" in flags  # E712/E711


@pytest.mark.parametrize(("change", "reason"), [
    ({"module_path": "pkg/core.py", "function_name": "core", "signature": "def core(value: int) -> int:"},
     "PLAN_WOULD_EDIT_EXISTING_FILE"),  # even the function-named fallback module exists
    ({"module_path": "extra.py"}, "PLAN_PATH_INVALID"),
    ({"module_path": "tests/extra.py"}, "PLAN_PATH_OUTSIDE_ENVELOPE"),
    ({"module_path": "pkg/../evil.py"}, "PLAN_PATH_INVALID"),
    ({"module_path": "pkg/review.py"}, "PLAN_PATH_FORBIDDEN_OR_UNDER_REVIEW"),
    ({"module_path": "nopkg/x.py"}, "PLAN_PATH_OUTSIDE_ENVELOPE"),
    ({"function_name": "import"}, "PLAN_FUNCTION_NAME_INVALID"),
    ({"signature": "def other(x):"}, "PLAN_SIGNATURE_INVALID"),
    ({"objective": "short"}, "PLAN_SPEC_INCOMPLETE"),
    ({"examples": GOOD_PLAN["examples"][:2]}, "PLAN_EXAMPLES_INSUFFICIENT"),
    ({"examples": [{"call": "other(1)", "why": "w", "expected": 1}] * 3}, "PLAN_EXAMPLES_INSUFFICIENT"),
    ({"examples": [{"call": "add_two(x)", "why": "w", "expected": 1}] * 3}, "PLAN_EXAMPLES_INSUFFICIENT"),
    ({"examples": [{"call": "add_two(__import__('os'))", "why": "w", "expected": 1}] * 3},
     "PLAN_EXAMPLES_INSUFFICIENT"),
    ({"examples": [{"call": "add_two(1)", "expected": 3}] * 3}, "PLAN_EXAMPLES_INSUFFICIENT"),  # no reasoning
    ({"examples": [{"call": "add_two(b'x')", "why": "w", "expected": 3}] * 3}, "PLAN_EXAMPLES_INSUFFICIENT"),
    ({"examples": [{"call": "add_two(1)", "why": "w", "expected": "b'x'"}] * 3}, "PLAN_EXAMPLES_INSUFFICIENT"),
    ({"examples": [{"call": "add_two(1, tolerance=0.5)", "why": "w", "expected": 3}] * 3},
     "PLAN_EXAMPLE_DOES_NOT_MATCH_SIGNATURE"),
    ({"examples": [{"call": "add_two(1, 2)", "why": "w", "expected": 3}] * 3}, "PLAN_EXAMPLE_DOES_NOT_MATCH_SIGNATURE"),
    ({"examples": [{"call": "add_two()", "why": "w", "expected": 3}] * 3}, "PLAN_EXAMPLE_DOES_NOT_MATCH_SIGNATURE"),
    ({"signature": "def add_two(value: int -> int:"}, "PLAN_SIGNATURE_INVALID"),
])
def test_unusable_plans_are_rejected_before_any_code(env, change, reason):
    runner = Runner(plan=lambda *_a: {**GOOD_PLAN, **change})
    step(env, runner, owned=lambda repo: ["pkg/review.py"])
    result = step(env, runner)
    assert result["worker_status"] == "FAIL" and reason in result["worker_summary"]


def test_a_malformed_model_reply_is_a_failed_plan_not_a_crash(env):
    def broken(*_a):
        raise ValueError("Expecting value: line 1")
    runner = Runner(plan=broken)
    step(env, runner)
    assert "PLAN_REJECTED" in step(env, runner)["worker_summary"]


def test_working_memory_is_bounded_and_merges_clear_blockers(env):
    for i in range(12):
        wm.record(env.state, "jweter/alpha", "TURN", packet_id=f"P{i}", note="x" * 2000, blocker="broke")
    row = wm.load(env.state)["repositories"]["jweter/alpha"]
    assert len(row["notes"]) == wm.MAX_NOTES_PER_REPO and len(row["notes"][-1]["note"]) == wm.MAX_NOTE_CHARS
    assert row["blockers"]
    wm.record(env.state, "jweter/alpha", "MERGED", outcome="MERGED")
    assert wm.load(env.state)["repositories"]["jweter/alpha"]["blockers"] == []
    assert len(wm.excerpt(env.state, "jweter/alpha")) <= 1500


def test_real_policy_opens_every_product_repository_but_not_the_engine_itself():
    config = pl.rotation_config(POLICY)
    assert config and config["exclude"] == ["jweter/project-orchestrator"]
    repos = pl.product_repositories(ROOT, config)
    assert "jweter/project-orchestrator" not in repos and len(repos) >= 6


def test_supervisor_runs_product_rotation_only_when_commissioning_is_idle(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pl, "advance_product_lane", lambda *a, **k: calls.append(k["allow_dispatch"]) or {})
    for status, allowed in (("IDLE_NO_READY_SLICE", True), ("IMPLEMENTING", False)):
        monkeypatch.setattr("project_orchestrator.iron_god_canon_lane.advance_commissioning_lane",
                            lambda *a, _s=status, **k: {"status": _s})
        supervisor_runtime.commissioning_products(tmp_path, ROOT, [], supervisor={}, release_sha="a" * 40)
    assert calls == [True, False]


def test_a_rejected_plan_gets_one_repair_with_the_exact_reason(env):
    seen = []

    def planner(worktree, packet, check, feedback=""):
        seen.append(feedback)
        return {**GOOD_PLAN, "examples": GOOD_PLAN["examples"][:1]} if not feedback else dict(GOOD_PLAN)
    runner = Runner(plan=planner)
    step(env, runner)
    assert step(env, runner)["status"] == "ACCEPTED_LOCAL"
    assert seen[0] == "" and "PLAN_EXAMPLES_INSUFFICIENT: 1 usable" in seen[1] and len(seen) == 2


def test_a_module_written_under_its_bare_name_is_moved_into_the_planned_path(env):
    def misplaced(worktree, message, files, check):
        check()
        (worktree / "pkg" / "extra.py").write_text("", encoding="utf-8")  # aider opened the planned path empty
        (worktree / "extra.py").write_text(GOOD_MODULE, encoding="utf-8")  # but wrote the code under the bare name
    runner = Runner(agent=misplaced)
    dispatched = step(env, runner)
    accepted = step(env, runner)
    assert accepted["status"] == "ACCEPTED_LOCAL", accepted
    clone = pl.ProductLane(env.state).clone(dispatched["repository"])
    assert "return value + 2" in git(clone, "show", f"{accepted['head_sha']}:pkg/extra.py")
    assert git(clone, "diff", "--name-only", dispatched["base_sha"], accepted["head_sha"]).splitlines() == [
        "pkg/extra.py", "tests/test_extra.py"]


def test_a_repository_failing_its_own_base_preflight_is_skipped_by_the_rotation(env):
    lane = pl.ProductLane(env.state)
    lane.root.mkdir(parents=True, exist_ok=True)
    not_ready = {r: {"ready": False} for r in REPOS[:2]}
    (lane.root / "readiness.json").write_text(json.dumps({"repositories": not_ready}), encoding="utf-8")
    assert step(env, Runner())["repository"] == REPOS[2]  # only the ready repository gets a turn


LOCK = """
[[package]]
name = "ruff"
version = "0.17.1"
groups = ["dev"]

[[package]]
name = "torch"
version = "2.14.1+cpu"
groups = ["main"]

[package.source]
type = "legacy"
url = "https://download.pytorch.org/whl/cpu"
reference = "pytorch-cpu"

[[package]]
name = "uvloop"
version = "0.21.0"
groups = ["main"]
markers = "sys_platform != 'win32'"

[[package]]
name = "localpkg"
version = "1.0"
groups = ["main"]

[package.source]
type = "directory"
url = "../localpkg"
"""


def test_poetry_lock_pins_keep_exact_versions_and_their_indexes_for_this_platform(tmp_path):
    lock = tmp_path / "poetry.lock"
    lock.write_text(LOCK, encoding="utf-8")
    pins = WORKER.poetry_lock_pins(lock)
    assert pins == ["--extra-index-url https://download.pytorch.org/whl/cpu", "ruff==0.17.1", "torch==2.14.1+cpu"]


def test_repository_environment_is_reused_until_the_lockfile_changes(env):
    lane = pl.ProductLane(env.state)
    clone, base = env.sync(lane, REPOS[0])
    worktree = env.tmp / "envwt"
    git(clone, "worktree", "add", "-q", "--detach", str(worktree), base)
    assert WORKER.repository_environment(worktree, lambda: None)["kind"] == "system"  # no lockfile: system Python
    (worktree / "poetry.lock").write_text(LOCK, encoding="utf-8")
    envdir = clone.parent / ".envs" / clone.name
    (envdir / "Scripts").mkdir(parents=True)
    (envdir / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    import hashlib
    digest = hashlib.sha256((worktree / "poetry.lock").read_bytes()).hexdigest()
    (envdir / ".iron-god-env.json").write_text(json.dumps({"lock_sha256": digest}), encoding="utf-8")
    reused = WORKER.repository_environment(worktree, lambda: None)
    assert reused["status"] == "REUSED" and reused["overrides"]["POETRY_VIRTUALENVS_CREATE"] == "false"
    assert reused["overrides"]["VIRTUAL_ENV"] == str(envdir)


def test_a_plan_reusing_an_existing_module_name_gets_a_new_module_named_after_its_function(env):
    packet, worktree = plan_for(env)
    plan = WORKER.validate_plan({**GOOD_PLAN, "module_path": "pkg/core.py"}, worktree, packet["base_sha"], packet)
    assert plan["module_path"] == "pkg/add_two.py" and plan["test_path"] == "tests/test_add_two.py"


def test_the_brief_offers_only_real_python_packages():
    listed = ["pkg/__init__.py", "pkg/core.py", "prototypes/tool/run.py", "tests/__init__.py", "src/app/__init__.py"]
    assert WORKER.python_packages(listed, ["pkg/", "prototypes/", "src/", "tests/"]) == ["pkg/", "src/app/"]


def test_a_repository_without_a_python_package_is_recorded_not_eligible(env, monkeypatch):
    monkeypatch.setattr(pl, "python_packages", lambda clone, base, roots: [])
    runner = Runner()
    result = step(env, runner)
    assert result["status"] == "NOT_ELIGIBLE_NO_PYTHON_PACKAGE" and runner.spawned == 0
    readiness = json.loads((pl.ProductLane(env.state).root / "readiness.json").read_text(encoding="utf-8"))
    row = readiness["repositories"][result["repository"]]
    assert row["ready"] is False and row["reason"] == "NO_PYTHON_PACKAGE"


SLIPPED_PLAN = {**GOOD_PLAN, "examples": [*GOOD_PLAN["examples"][:2],
                                          {"call": "add_two(-2)", "why": "slip", "expected": 1}]}


def test_a_planner_slip_is_corrected_only_when_an_independent_derivation_agrees_with_the_code(env):
    runner = Runner(plan=lambda *_a: dict(SLIPPED_PLAN), rederive=lambda packet, plan, call: 0)
    dispatched = step(env, runner)
    accepted = step(env, runner)
    assert accepted["status"] == "ACCEPTED_LOCAL", accepted
    clone = pl.ProductLane(env.state).clone(dispatched["repository"])
    assert "assert add_two(-2) == 0" in git(clone, "show", f"{accepted['head_sha']}:tests/test_extra.py")
    github = FakeGitHub()
    step(env, runner, github, allow=False)
    assert "add_two(-2) returns 0 (plan said 1)" in github.opened[0]["body"]


@pytest.mark.parametrize("fresh", [1, WORKER.NO_OPINION, 5])
def test_no_correction_without_agreement_between_code_and_a_fresh_derivation(env, fresh):
    runner = Runner(plan=lambda *_a: dict(SLIPPED_PLAN), rederive=lambda packet, plan, call: fresh)
    step(env, runner)
    result = step(env, runner)
    assert result["worker_status"] == "FAIL" and result["worker_summary"] == "SLICE_TESTS_FAILED"


def test_no_correction_when_the_code_disagrees_with_most_examples(env):
    runner = Runner(agent=lambda w, m, f, c: good_agent(w, m, f, c, source=WRONG_MODULE),
                    rederive=lambda packet, plan, call: eval(call.replace("add_two", "int")))
    step(env, runner)
    assert step(env, runner)["worker_summary"] == "SLICE_TESTS_FAILED"


def test_review_mode_leaves_a_green_pull_request_for_the_owner(env):
    github = FakeGitHub()
    review = {**env.policy, "portfolio_rotation": {**env.policy["portfolio_rotation"], "merge": "review"}}
    env.policy = review
    runner = Runner()
    first = step(env, runner, github)["repository"]
    step(env, runner, github)
    step(env, runner, github, allow=False)  # push + PR
    step(env, runner, github, allow=False)  # CI green -> waits for the owner
    item = next(iter(pl.ProductLane(env.state).publications()["items"].values()))
    assert item["state"] == "AWAITING_OWNER_REVIEW" and github.merged == []
    assert wm.load(env.state)["repositories"][first]["last_outcome"] == "CI_GREEN_AWAITING_OWNER"
    assert step(env, runner, github)["status"] == "DISPATCHED"  # the rotation keeps going meanwhile


def test_real_policy_records_the_owners_merge_decision():
    assert pl.rotation_config(POLICY)["merge"] in {"auto", "review"}


def test_failed_attempts_are_remembered_so_the_next_planner_chooses_something_different(env):
    briefs = []

    def failing_plan(worktree, packet, check, feedback=""):
        briefs.append(packet["working_memory"])
        return dict(GOOD_PLAN)
    runner = Runner(plan=failing_plan, preflight_ok=False)
    repo = step(env, runner)["repository"]
    step(env, runner)
    board = wm.load(env.state)["repositories"][repo]
    assert board["attempts"][-1]["attempt"] == "add_two in pkg/extra.py"
    assert "already attempted (choose something different): add_two in pkg/extra.py" in wm.excerpt(env.state, repo)


def test_a_plan_repeating_an_already_attempted_function_is_sent_back_for_a_different_one(env):
    repo = REPOS[0]
    for r in REPOS:  # whichever repository the rotation picks has already tried add_two
        wm.record(env.state, r, "TURN_FAIL", outcome="FAIL", attempt="add_two in pkg/extra.py")
    seen = []

    def repeats_first(worktree, packet, check, feedback=""):
        seen.append(feedback)
        if not feedback:
            return dict(GOOD_PLAN)  # add_two again
        return {**GOOD_PLAN, "function_name": "add_three", "signature": "def add_three(value: int) -> int:",
                "examples": [{"call": "add_three(1)", "why": "1+3", "expected": 4},
                             {"call": "add_three(0)", "why": "0+3", "expected": 3},
                             {"call": "add_three(-3)", "why": "-3+3", "expected": 0}]}
    runner = Runner(plan=repeats_first, agent=lambda w, m, f, c: (w / f[0]).write_text(
        'def add_three(value: int) -> int:\n    """Add three."""\n    return value + 3\n', encoding="utf-8"))
    step(env, runner)
    assert step(env, runner)["status"] == "ACCEPTED_LOCAL"
    assert "PLAN_ALREADY_ATTEMPTED: add_two" in seen[1] and repo in REPOS


def test_planning_prompt_explicitly_excludes_previous_attempts() -> None:
    packet = {"repository": "jweter/knowledge-engine-core", "attempted_functions": [
        "normalize_text", "clean_text", "normalize_text"]}
    prompt = WORKER.planning_prompt(packet, "ROADMAP BRIEF")
    assert "never propose these" in prompt
    assert "clean_text, normalize_text" in prompt
    assert "rejects all 2 attempted names" in prompt
    assert prompt.index("clean_text") < prompt.index("ROADMAP BRIEF")


def test_planning_prompt_bounds_large_exclusion_list_without_weakening_validation() -> None:
    packet = {"repository": "jweter/knowledge-engine-ai", "attempted_functions": [
        f"attempt_{i:03d}" for i in range(75)]}
    prompt = WORKER.planning_prompt(packet, "ROADMAP BRIEF")
    assert "attempt_000" in prompt
    assert "attempt_049" in prompt
    assert "attempt_050" not in prompt
    assert "rejects all 75 attempted names" in prompt
    assert "(none)" in WORKER.planning_prompt({"repository": "jweter/VoiceStudio"}, "BRIEF")
    assert "rejects all 0 attempted names" in WORKER.planning_prompt(
        {"repository": "jweter/VoiceStudio", "attempted_functions": None}, "BRIEF")
