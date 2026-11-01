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

from project_orchestrator import iron_god_backlog as backlog_mod
from project_orchestrator import iron_god_development_memory as devmem
from project_orchestrator import iron_god_merge_readiness as readiness
from project_orchestrator import iron_god_product_lane as pl
from project_orchestrator import iron_god_product_metrics as metrics
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
    """A pull request as GitHub reports it: head = last push, unresolved threads block the mergeable state."""

    CONCLUSION = {"pass": "SUCCESS", "skipping": "SKIPPED", "fail": "FAILURE", "cancel": "CANCELLED"}

    def __init__(self, buckets=("pass",), threads=(), merge_state="CLEAN", fail_merge=False, settled=True):
        self.buckets = list(buckets)
        self.threads = [dict(t) for t in threads]
        self.merge_state, self.fail_merge, self.settled = merge_state, fail_merge, settled
        self.pushed, self.opened, self.merged, self.resolved = [], [], [], []
        self.heads, self.branches = {}, {}
        self.merger = readiness.load_merger(ROOT)  # the real, shipped green_pr_merger rules

    def push(self, clone, branch, head):
        git(clone, "push", "-q", "origin", f"{head}:refs/heads/{branch}")  # fast-forward only, like production
        self.pushed.append((branch, head))
        self.heads[branch] = head

    def open_pr(self, repo, branch, title, body):
        self.opened.append({"repo": repo, "branch": branch, "title": title, "body": body})
        number = len(self.opened)
        self.branches[number] = branch
        return {"url": f"https://github.com/{repo}/pull/{number}", "number": number}

    def checks(self, repo, number):
        return [{"name": f"ci-{i}", "state": b.upper(), "bucket": b} for i, b in enumerate(self.buckets)]

    def pull_request(self, repo, number):
        unresolved = any(not t["resolved"] for t in self.threads)
        return {"number": number, "headRefOid": self.heads[self.branches[number]], "baseRefName": "main",
                "state": "OPEN", "isDraft": False, "reviewDecision": None,
                "mergeStateStatus": "BLOCKED" if unresolved else self.merge_state,
                "statusCheckRollup": [{"status": "COMPLETED" if b in self.CONCLUSION else "IN_PROGRESS",
                                       "conclusion": self.CONCLUSION.get(b, "")} for b in self.buckets],
                "createdAt": "2020-01-01T00:00:00Z", "author": {"login": "jweter"}}

    def review_threads(self, repo, number):
        return [dict(t) for t in self.threads]

    def review_settled(self, repo, number, head, created_at):
        return self.settled

    def age_seconds(self, created_at):
        return 9999.0

    def resolve_thread(self, thread_id):
        self.resolved.append(thread_id)
        for t in self.threads:
            if t["id"] == thread_id:
                t["resolved"] = True

    def merge(self, repo, number, head):
        if self.fail_merge:
            raise RuntimeError("MERGE_FAILED: GraphQL: Base branch was modified")
        self.merged.append((repo, number, head))
        return "f" * 40


def review_thread(thread_id, head, body="P1 incorrect result for negative input", path="pkg/extra.py"):
    return {"id": thread_id, "resolved": False, "path": path, "line": 3,
            "comments": [{"body": body, "commit": head}]}


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

    # The self-planning path stays covered; backlog tests opt into the real policy's roadmap planning.
    policy = {**POLICY, "portfolio_rotation": {**POLICY["portfolio_rotation"], "planning": "self"}}
    return SimpleNamespace(release=release, state=state, pool=pool, sync=sync, remotes=remotes, tmp=tmp_path,
                           memory=tmp_path / "shared-memory", policy=policy)


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
                 rederive=lambda packet, plan, call: WORKER.NO_OPINION, generate=None):
        self.agent, self.preflight_ok, self.gate, self.plan = agent, preflight_ok, gate, plan
        self.rederive, self.generate = rederive, generate
        self.spawned = 0

    def spawn(self, release_repo, packet_path, handback_path, *, release_sha, log_path):
        self.spawned += 1
        packet = json.loads(Path(packet_path).read_text(encoding="utf-8"))
        handback = WORKER.execute_product_packet(
            packet, authority=lambda: True, plan=self.plan, run_agent=self.agent, gates=lambda *_a: self.gate,
            rederive=self.rederive, format_files=lambda *_a: "none", generate=self.generate,
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

[[package]]
name = "knowledge-engine-ai"
version = "0.1.0"
groups = ["main"]

[package.source]
type = "git"
url = "https://github.com/jweter/knowledge-engine-ai.git"
reference = "2eee4cc"
resolved_reference = "2eee4cc9ba76f99e669d2e52669e84d8b5199a41"
"""


def test_poetry_lock_pins_keep_exact_versions_and_their_indexes_for_this_platform(tmp_path):
    lock = tmp_path / "poetry.lock"
    lock.write_text(LOCK, encoding="utf-8")
    pins = WORKER.poetry_lock_pins(lock)
    assert pins == ["--extra-index-url https://download.pytorch.org/whl/cpu",
                    "knowledge-engine-ai @ git+https://github.com/jweter/knowledge-engine-ai.git"
                    "@2eee4cc9ba76f99e669d2e52669e84d8b5199a41", "ruff==0.17.1", "torch==2.14.1+cpu"]


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
    digest = hashlib.sha256(WORKER.ENV_RECIPE.encode() + (worktree / "poetry.lock").read_bytes()).hexdigest()
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


# Roadmap-driven backlog (2026-10-27): only approved, consumer-linked slices are built, exactly as written.

def backlog_item(item_id, repo, *, status="approved", level=1, build=True, depends_on=(), assignee="iron-god"):
    item = {"id": item_id, "repository": repo, "title": f"{item_id} title", "roadmap_ref": "docs/ROADMAP.md slice 1",
            "addresses": "a roadmap gap", "why": "it unblocks the workflow", "consumer": "the export workflow step",
            "objective": "Add add_two to pkg/extra.py for the export workflow.", "interfaces": ["pkg/core.py"],
            "depends_on": list(depends_on), "acceptance_criteria": ["examples hold"], "tests": ["tests/test_extra.py"],
            "level": level, "complexity": "low", "verification": "preflight", "definition_of_done": "merged",
            "status": status, "assignee": assignee}
    if build:
        item["build"] = {k: GOOD_PLAN[k] for k in ("module_path", "function_name", "signature", "objective",
                                                   "examples", "note")}
    return item


def backlog_env(env, items):
    path = env.release / backlog_mod.BACKLOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": items}), encoding="utf-8")
    env.policy = {**POLICY, "portfolio_rotation": {**POLICY["portfolio_rotation"], "planning": "backlog",
                                                   "development_level": 1}}
    return env


def never_plans(worktree, packet, check, feedback=""):
    raise AssertionError("an approved backlog slice must never be re-planned by the model")


def test_backlog_mode_builds_only_approved_items_exactly_as_written_and_links_the_pr_to_the_roadmap(env):
    backlog_env(env, [backlog_item("P-1", REPOS[0], status="proposed"),
                      backlog_item("L2-1", REPOS[2], level=2, build=False),  # beyond the measured level
                      backlog_item("RS-1", REPOS[1])])  # shortest name: Windows path limits in pytest's tmp
    runner, github = Runner(plan=never_plans), FakeGitHub(("pass",))
    dispatched = step(env, runner, github)
    assert dispatched["status"] == "DISPATCHED" and dispatched["repository"] == REPOS[1], dispatched.get("detail")
    assert dispatched["backlog_item"] == "RS-1"
    assert step(env, runner, github)["status"] == "ACCEPTED_LOCAL"
    step(env, runner, github, allow=False)  # push + PR
    pr = github.opened[0]
    assert pr["title"].startswith("Iron God [RS-1]") and "Consumer: the export workflow step" in pr["body"]
    assert "Roadmap: docs/ROADMAP.md slice 1" in pr["body"]
    step(env, runner, github, allow=False)  # CI green -> merge
    assert backlog_mod.load_state(pl.ProductLane(env.state).root)["items"]["RS-1"]["outcome"] == "MERGED"
    assert step(env, runner, github)["status"] == "IDLE_NO_APPROVED_BACKLOG_ITEM"  # never invents work
    assert runner.spawned == 1


def test_a_backlog_item_waits_for_its_dependencies_and_stops_after_its_attempts(env):
    backlog_env(env, [backlog_item("A", REPOS[2], status="proposed"),
                      backlog_item("B", REPOS[2], depends_on=["A"]), backlog_item("C", REPOS[1])])
    # Distinct failed implementations must still consume the original bounded
    # retry budget; identical failures are covered by the separate early-stop
    # regression in test_iron_god_failed_source.py.
    attempts = iter(range(backlog_mod.MAX_ITEM_ATTEMPTS))

    def distinct_failed_agent(worktree, message, files, check):
        good_agent(worktree, message, files, check,
                   source=GOOD_MODULE + f"\n# failed-variant-{next(attempts)}\n")

    runner = Runner(agent=distinct_failed_agent, plan=never_plans, preflight_ok=False)
    for _ in range(backlog_mod.MAX_ITEM_ATTEMPTS):
        assert step(env, runner)["backlog_item"] == "C"  # B waits for A, which is not approved
        step(env, runner)  # harvest the failed attempt
    assert step(env, runner)["status"] == "IDLE_NO_APPROVED_BACKLOG_ITEM"
    state = backlog_mod.load_state(pl.ProductLane(env.state).root)["items"]["C"]
    assert state["attempts"] == backlog_mod.MAX_ITEM_ATTEMPTS and state["outcome"].startswith("FAIL")


@pytest.mark.parametrize(("change", "problem"), [
    ({"consumer": "tbd"}, "CONSUMER_UNIDENTIFIED"),
    ({"build": None}, "APPROVED_ITEM_WITHOUT_BUILD_SPEC"),
    ({"objective": "x" * 1200}, "TEXT_OVER_CANON_BOUND"),
    ({"level": 7}, "LEVEL_INVALID"),
    ({"level": True}, "LEVEL_INVALID"),  # bool is an int subclass, never a build level
    ({"status": "maybe"}, "STATUS_INVALID"),
])
def test_malformed_backlog_items_are_never_dispatched(change, problem):
    assert problem in backlog_mod.item_problems({**backlog_item("X", REPOS[0]), **change})


def test_duplicate_backlog_id_rejects_all_occurrences_even_if_first_is_valid(env):
    first = backlog_item("RS-DUP", REPOS[1])
    second = backlog_item("RS-DUP", REPOS[2])
    backlog_env(env, [first, second])
    items, rejected = backlog_mod.load_backlog(env.release)
    assert items == []  # ambiguous ids must never enter the dispatchable pool
    assert "DUPLICATE_ID" in rejected["RS-DUP"]


def test_real_backlog_covers_every_product_repository_and_is_well_formed():
    items, rejected = backlog_mod.load_backlog(ROOT)
    assert rejected == {}
    repos = {item["repository"] for item in items}
    assert all(sum(1 for i in items if i["repository"] == r) >= 5 for r in repos) and len(repos) == 6
    assert pl.rotation_config(POLICY)["planning"] == "backlog"
    assert pl.rotation_config(POLICY)["development_level"] == 2  # the owner-approved directed Level 2 pilot
    for item in items:  # every approved Level 2 item is a directed edit, never a self-planned change
        if item["status"] == "approved" and item["level"] == 2:
            assert item["direct"]["test_source"] and len(item["direct"]["targets"]) <= 2
    by_id = {item["id"]: item for item in items}
    assert all(item["assignee"] for item in items if item["status"] == "approved")
    for item in items:  # a retired slice names its successor and keeps its failure evidence
        if item.get("superseded_by"):
            successor = by_id[item["superseded_by"]]
            assert item["status"] == "retired" and successor["supersedes"] == item["id"]
            assert item["retirement"]["packet_id"] and item["retirement"]["outcome"]
        completion = item.get("completion") or {}
        if completion.get("kind") == "COMPLETED_EXTERNALLY":  # done only with verified merge evidence
            assert item["status"] == "done" and completion["pull_request"] and completion["verification"]
            assert len(completion["merge_commit"]) == 40
    everward =[i for i in items if i["repository"] == "jweter/Project-Everward"]
    assert all(i["level"] not in backlog_mod.BUILDABLE_LEVELS or i["status"] != "approved" for i in everward)


def test_metrics_count_a_merged_helper_as_progress_only_when_something_uses_it(env):
    backlog_env(env, [backlog_item("RS-1", REPOS[1])])
    runner, github = Runner(plan=never_plans), FakeGitHub(("pass",))
    for allow in (True, True, False, False):
        step(env, runner, github, allow=allow)
    report = metrics.compute(env.state, env.release)
    assert report["merged_pull_requests"] == 1 and report["backlog"]["roadmap_items_completed"] == 1
    assert report["backlog"]["used_by_product"] == 0  # a level-1 helper whose consumer has not merged yet
    assert report["unlinked_merges_not_counted_as_progress"] == 0


def test_lint_failures_reach_the_bounded_repair_as_exact_lines(env, monkeypatch):
    monkeypatch.setattr(WORKER, "quick_test", lambda *_a, **_k: {"ok": True, "command": "pytest", "output_tail": ""})
    monkeypatch.setattr(WORKER, "type_check", lambda *_a: None)
    monkeypatch.setattr(WORKER, "lint_check", lambda *_a: {
        "ok": False, "command": "python -m ruff check", "returncode": 1,
        "output_tail": "pkg/extra.py:3:101: E501 Line too long (120 > 100)\nFound 1 error."})
    result = WORKER.slice_checks(env.tmp, "pkg/extra.py", "tests/test_extra.py", lambda: None)
    assert result["output_tail"] == "E ruff: pkg/extra.py:3:101: E501 Line too long (120 > 100)"


def test_an_example_using_the_key_b_is_not_mistaken_for_bytes(env):
    packet, worktree = plan_for(env)
    plan = {**GOOD_PLAN, "function_name": "count_keys", "objective": "Return how many pairs are given.",
            "signature": "def count_keys(pairs: list[tuple[str, int]]) -> int:",
            "examples": [{"call": "count_keys([('a', 1), ('b', 2)])", "why": "two pairs", "expected": 2},
                         {"call": "count_keys([('b', 1)])", "why": "one pair", "expected": 1},
                         {"call": "count_keys([])", "why": "none", "expected": 0}]}
    assert len(WORKER.validate_plan(plan, worktree, packet["base_sha"], packet)["examples"]) == 3
    with pytest.raises(WORKER.PlanRejected):
        WORKER.validate_plan({**plan, "examples": [{**e, "call": "count_keys(b'ab')"} for e in plan["examples"]]},
                             worktree, packet["base_sha"], packet)


DIRECT_TEST = "from pkg.core import one\n\n\ndef test_one_is_two() -> None:\n    assert one() == 2\n"


def direct_item(item_id, repo, **direct):
    item = backlog_item(item_id, repo, level=2, build=False)
    item["direct"] = {"module_path": "pkg/core.py", "targets": ["one"], "add": [],
                      "direction": "one() must return 2 instead of 1.", "test_path": "tests/test_one_is_two.py",
                      "test_source": DIRECT_TEST, **direct}
    return item


def directed_env(env, items):
    backlog_env(env, items)
    env.policy["portfolio_rotation"]["development_level"] = 2
    return env


def replying(text, seen=None):
    def generate(prompt, num_predict, check):
        check()
        if seen is not None:
            seen.append(prompt)
        return {"text": text, "prompt_tokens": 10, "output_tokens": 5, "model_s": 0.1}
    return generate


def no_model(*_args):
    pytest.fail("the model must not be called")


def test_an_approved_directed_slice_changes_only_its_unit_and_is_published_with_its_roadmap_link(env):
    directed_env(env, [direct_item("RS-2", REPOS[1])])
    prompts = []
    runner = Runner(plan=never_plans, agent=None, generate=replying("```python\ndef one():\n    return 2\n```", prompts))
    github = FakeGitHub(("pass",))
    assert step(env, runner, github)["backlog_item"] == "RS-2"
    accepted = step(env, runner, github)
    assert accepted["status"] == "ACCEPTED_LOCAL", accepted
    assert "def one():\n    return 1" in prompts[0] and DIRECT_TEST not in prompts[0]  # the unit, never the tests
    step(env, runner, github, allow=False)
    assert github.opened[0]["title"].startswith("Iron God [RS-2]")
    clone = pl.ProductLane(env.state).clone(REPOS[1])
    head = github.pushed[0][1]
    assert sorted(git(clone, "diff", "--name-only", f"{head}~1", head).split()) == ["pkg/core.py",
                                                                                     "tests/test_one_is_two.py"]
    assert git(clone, "show", f"{head}:tests/test_one_is_two.py") + "\n" == DIRECT_TEST


@pytest.mark.parametrize(("reply", "summary"), [
    ("```python\ndef one():\n    return 2\n```\n```python\ndef one():\n    return 3\n```", "DUPLICATE_UNIT"),
    ("```python\ndef two():\n    return 2\n```", "UNEXPECTED_UNIT: two"),
    ("no code", "NO_CODE_BLOCKS"),
    ("```python\ndef one():\n    return 3\n```", "SLICE_TESTS_FAILED"),
])
def test_a_failed_directed_slice_is_never_retried_and_never_committed(env, reply, summary):
    directed_env(env, [direct_item("RS-2", REPOS[1])])
    runner = Runner(plan=never_plans, agent=None, generate=replying(reply))
    step(env, runner)
    harvested = step(env, runner)
    assert harvested["worker_status"] == "FAIL" and summary in harvested["worker_summary"]
    assert step(env, runner)["status"] == "IDLE_NO_APPROVED_BACKLOG_ITEM"  # back to planning, not a retry loop
    diagnostics = next(pl.ProductLane(env.state).reports.glob("*-handback-diagnostics.json"))
    assert json.loads(diagnostics.read_text(encoding="utf-8"))["directed"]["reply"] == reply[:8000]
    lane_root = pl.ProductLane(env.state).root  # the attempt's observed facts reach the development memory
    assert devmem.stats(lane_root)["attempts"] == 1
    with devmem.closing(devmem._connect(lane_root)) as connection:
        prompt, saved = connection.execute("SELECT prompt, reply FROM attempts").fetchone()
    assert "Rules:" in prompt and saved == reply[:8000]


def test_a_directed_slice_whose_tests_already_pass_on_main_is_not_built(env):
    passing = DIRECT_TEST.replace("== 2", "== 1")
    directed_env(env, [direct_item("RS-2", REPOS[1], test_source=passing)])
    runner = Runner(plan=never_plans, agent=None, generate=no_model)
    step(env, runner)
    assert "DIRECTED_TEST_NOT_DISCRIMINATING" in step(env, runner)["worker_summary"]


@pytest.mark.parametrize(("change", "problem"), [
    ({"test_path": "tests/test_core.py"}, "DIRECT_TEST_WOULD_OVERWRITE"),
    ({"module_path": "tools/gen.py"}, "DIRECT_MODULE_OUTSIDE_ENVELOPE"),
    ({"module_path": "engineering/preflight.py"}, "DIRECT_PATH_FORBIDDEN"),
    ({"targets": ["missing"]}, "DIRECT_UNITS_UNKNOWN"),
    ({"targets": ["one", "imports", "x", "y"]}, "DIRECT_UNITS_OUT_OF_BOUNDS"),
    ({"test_source": "x = 1\n"}, "DIRECT_TEST_INVALID"),
])
def test_an_invalid_direction_is_rejected_before_the_model_is_called(env, change, problem):
    directed_env(env, [direct_item("RS-2", REPOS[1], **change)])
    runner = Runner(plan=never_plans, agent=None, generate=no_model)
    step(env, runner)
    harvested = step(env, runner)
    assert harvested["worker_summary"].startswith("BACKLOG_SLICE_INVALID") and problem in harvested["worker_summary"]


@pytest.mark.parametrize(("files", "expected"), [
    ({"pyproject.toml": "[tool.mypy]\nstrict = true\n[tool.ruff]\nline-length = 100\n"}, ["strict", "100"]),
    ({"pyproject.toml": "[tool.mypy]\nwarn_unused_ignores = true\n"}, []),
    ({"mypy.ini": "[mypy]\ndisallow_untyped_defs = True\n", "ruff.toml": "line-length = 88\n"}, ["strict", "88"]),
    ({"pyproject.toml": "not toml ["}, []),
    ({}, []),
])
def test_quality_rules_state_only_the_gates_the_repository_configures(tmp_path, files, expected):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    rules = WORKER.quality_rules(tmp_path)
    assert len(rules) == len(expected)
    assert all(word in rule for word, rule in zip(expected, rules, strict=True))
    if "strict" in expected:
        assert "-> None" in rules[0] and "list[str]" in rules[0]


def test_the_preflight_sees_a_ci_width_console_whatever_the_worker_console_is(tmp_path, monkeypatch):
    # 2026-10-29 KEC-9: Rich wrapped a CLI error to the hidden console's width and two product tests failed
    seen = {}
    monkeypatch.setenv("COLUMNS", "20")
    monkeypatch.setattr(WORKER, "repository_environment", lambda worktree, check: {
        "python": "python", "kind": "uv", "overrides": {}})
    monkeypatch.setattr(WORKER.NativeWindowsRuntime, "execute_supervised",
                        lambda self, request, check, interval: seen.update(request.env) or SimpleNamespace(
                            returncode=0, stdout="", stderr=""))
    (tmp_path / "wt").mkdir()
    assert WORKER.production_preflight(tmp_path / "wt", lambda: None, 60)["ok"]
    assert seen["COLUMNS"] == "80" and seen["LINES"] == "24"


REAL_RELEASE_BUILDER_MODEL = WORKER.release_builder_model


@pytest.fixture(autouse=True)
def _never_unload_the_real_builder_model(monkeypatch):
    # the suite must not reach a live Ollama on the developer machine
    monkeypatch.setattr(WORKER, "release_builder_model", lambda *a, **k: False)


class _OllamaReply:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return b'{"done": true}'


def test_the_builder_model_is_unloaded_through_ollama_while_holding_the_model_slot(tmp_path, monkeypatch):
    # 2026-10-31: the 5 GB builder model stayed resident beside mypy and left 404 MB free
    from scripts.autonomous_slice_builder import MODEL

    slot = tmp_path / "slot"
    monkeypatch.setenv("EXPONENTIAL_OLLAMA_LOCK_DIR", str(slot))
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:1")
    sent = []

    def urlopen(request, timeout):
        sent.append((request.full_url, json.loads(request.data), slot.is_dir()))
        return _OllamaReply()

    monkeypatch.setattr(WORKER.urllib.request, "urlopen", urlopen)
    assert REAL_RELEASE_BUILDER_MODEL() is True
    assert sent == [("http://127.0.0.1:1/api/generate", {"model": MODEL.split("/", 1)[-1], "keep_alive": 0}, True)]
    assert not slot.exists()  # the slot is handed back


def test_a_generation_in_progress_is_never_interrupted_by_the_unload(tmp_path, monkeypatch):
    slot = tmp_path / "slot"
    slot.mkdir()
    monkeypatch.setenv("EXPONENTIAL_OLLAMA_LOCK_DIR", str(slot))
    monkeypatch.setattr(WORKER.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("the model slot is held by another process"))
    assert REAL_RELEASE_BUILDER_MODEL() is False
    assert slot.is_dir()  # not ours: left exactly as found


@pytest.mark.parametrize("failure", [OSError("refused"), TimeoutError(), ValueError("bad"),
                                     __import__("http.client").client.RemoteDisconnected("gone")])
def test_an_unreachable_ollama_never_fails_the_gate_and_releases_the_slot(tmp_path, monkeypatch, failure):
    slot = tmp_path / "slot"
    monkeypatch.setenv("EXPONENTIAL_OLLAMA_LOCK_DIR", str(slot))

    def urlopen(*_a, **_k):
        raise failure

    monkeypatch.setattr(WORKER.urllib.request, "urlopen", urlopen)
    assert REAL_RELEASE_BUILDER_MODEL() is False
    assert not slot.exists()


def test_the_model_is_unloaded_before_the_full_preflight_runs(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(WORKER, "release_builder_model", lambda *a, **k: order.append("unload") or True)
    monkeypatch.setattr(WORKER, "repository_environment", lambda worktree, check: {
        "python": "python", "kind": "uv", "overrides": {}})
    monkeypatch.setattr(WORKER.NativeWindowsRuntime, "execute_supervised",
                        lambda self, request, check, interval: order.append("preflight") or SimpleNamespace(
                            returncode=0, stdout="", stderr=""))
    (tmp_path / "wt").mkdir()
    assert WORKER.production_preflight(tmp_path / "wt", lambda: None, 60)["ok"]
    assert order == ["unload", "preflight"]


def test_the_model_is_unloaded_before_the_type_check_runs(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(WORKER, "release_builder_model", lambda *a, **k: order.append("unload") or True)
    monkeypatch.setattr(WORKER, "mypy_configured", lambda worktree: True)
    monkeypatch.setattr(WORKER, "repository_environment", lambda worktree, check: {
        "python": "python", "kind": "uv", "overrides": {}})
    monkeypatch.setattr(WORKER.NativeWindowsRuntime, "execute_supervised",
                        lambda self, request, check, interval: order.append("mypy") or SimpleNamespace(
                            returncode=0, stdout="", stderr=""))
    (tmp_path / "wt").mkdir()
    assert WORKER.type_check(tmp_path / "wt", ["pkg/mod.py"], lambda: None)["ok"]
    assert order == ["unload", "mypy"]


def test_only_items_assigned_to_iron_god_are_dispatched_and_other_claims_reserve_their_files(env):
    # 2026-10-28: another agent built KEC-6 while it was approved for Iron God
    theirs = backlog_item("RS-1", REPOS[1], assignee="chatgpt-dowork")
    backlog_env(env, [theirs, backlog_item("RS-2", REPOS[1])])
    assert step(env, Runner(plan=never_plans))["status"] == "IDLE_NO_APPROVED_BACKLOG_ITEM"  # same file, reserved
    items, rejected = backlog_mod.load_backlog(env.release)
    assert rejected == {} and backlog_mod.conflicts(items[1], items) == ["RS-1"]
    rows = {row["id"]: row for row in backlog_mod.ownership(items, {})}
    assert rows["RS-1"]["reservation"] == {"by": "chatgpt-dowork", "outcome": "ASSIGNED"}
    assert rows["RS-2"]["conflicts"] == ["RS-1"] and rows["RS-2"]["files"] == ["pkg/core.py", "pkg/extra.py"]
    elsewhere = backlog_item("RS-1", REPOS[1], assignee="chatgpt-dowork")
    elsewhere["interfaces"], elsewhere["build"]["module_path"] = ["pkg/other.py"], "pkg/other.py"
    backlog_env(env, [elsewhere, backlog_item("RS-2", REPOS[1])])
    assert step(env, Runner(plan=never_plans))["backlog_item"] == "RS-2"  # disjoint files: not blocked
    row = next(r for r in backlog_mod.ownership(*_live(env)) if r["id"] == "RS-2")
    assert row["reservation"]["by"] == "iron-god" and row["reservation"]["outcome"] == "DISPATCHED"


def _live(env):
    items, _rejected = backlog_mod.load_backlog(env.release)
    return items, backlog_mod.load_state(pl.ProductLane(env.state).root)


def test_an_approved_item_needs_an_assignee_and_the_ownership_command_filters_by_path(env, capsys):
    item = backlog_item("X", REPOS[0], assignee="")
    assert "APPROVED_ITEM_WITHOUT_ASSIGNEE" in backlog_mod.item_problems(item)
    assert "APPROVED_ITEM_WITHOUT_ASSIGNEE" not in backlog_mod.item_problems({**item, "status": "proposed"})
    backlog_env(env, [backlog_item("RS-1", REPOS[1]), {**backlog_item("RS-2", REPOS[2]), "interfaces": ["a.py"]}])
    assert backlog_mod.main(["--release-repo", str(env.release), "--lane-root", str(env.state / "none"),
                             "--path", "a.py"]) == 0
    table = json.loads(capsys.readouterr().out)
    assert table["agent"] == "iron-god" and [row["id"] for row in table["items"]] == ["RS-2"]


def advance_main(env, repo, files, message="another developer's change"):
    """Another developer merges a change to the 'GitHub' main branch."""
    work = env.tmp / "other-dev" / repo.split("/")[1]
    if not work.exists():
        git(env.tmp, "clone", "-q", str(env.remotes[repo]), str(work))
        git(work, "config", "user.email", "d@example.com")
        git(work, "config", "user.name", "Dev")
    git(work, "pull", "-q", "origin", "main")
    for name, text in files.items():
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(text, encoding="utf-8")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", message)
    git(work, "push", "-q", "origin", "HEAD:main")
    return git(work, "rev-parse", "HEAD")


def main_sha(env, repo):
    return git(env.remotes[repo], "rev-parse", "main")


@pytest.mark.parametrize("with_test", [False, True])
def test_a_slice_completed_by_another_developer_is_verified_on_main_and_unblocks_its_dependents(env, with_test):
    # 2026-10-28: KEC-6 merged elsewhere as PR #553 while KEC-7 waited on it
    repo = REPOS[1]
    approved = main_sha(env, repo)
    first = direct_item("RS-2", repo, approved_base=approved)
    second = {**direct_item("RS-3", repo, test_path="tests/test_one_again.py", approved_base=approved),
              "depends_on": ["RS-2"]}
    directed_env(env, [first, second])
    merged = advance_main(env, repo, {"pkg/core.py": "def one():\n    return 2\n",
                                      **({"tests/test_one_is_two.py": DIRECT_TEST} if with_test else {})})
    runner = Runner(plan=never_plans, agent=None, generate=no_model)
    assert step(env, runner)["backlog_item"] == "RS-2"
    harvested = step(env, runner)
    assert harvested["worker_summary"].startswith("COMPLETED_EXTERNALLY") and merged[:12] in harvested["worker_summary"]
    items, state = _live(env)
    assert state["items"]["RS-2"]["outcome"].startswith("COMPLETED_EXTERNALLY") and backlog_mod.is_done(items[0], state)
    rows = {row["id"]: row for row in backlog_mod.ownership(items, state)}
    assert rows["RS-2"]["state"] == "COMPLETED_EXTERNALLY"
    diagnostics = pl.ProductLane(env.state).reports / f"{harvested['packet_id']}-handback-diagnostics.json"
    evidence = json.loads(diagnostics.read_text(encoding="utf-8"))["external_completion"]
    assert evidence["module_commits"] == [merged] and evidence["approved_base"] == approved
    assert step(env, runner)["backlog_item"] == "RS-3"  # the dependent is unblocked


def test_external_completion_is_never_claimed_without_a_verified_change_to_the_module(env):
    repo = REPOS[1]
    advance_main(env, repo, {"pkg/core.py": "def one():\n    return 2\n"})
    approved = main_sha(env, repo)  # the approval base already passes: the tests never discriminated
    directed_env(env, [direct_item("RS-2", repo, approved_base=approved)])
    runner = Runner(plan=never_plans, agent=None, generate=no_model)
    step(env, runner)
    assert step(env, runner)["worker_summary"].startswith("DIRECTED_TEST_NOT_DISCRIMINATING")
    items, state = _live(env)
    assert not backlog_mod.is_done(items[0], state)


def test_an_approved_test_already_on_main_that_still_fails_needs_revalidation(env):
    repo = REPOS[1]
    approved = main_sha(env, repo)
    advance_main(env, repo, {"tests/test_one_is_two.py": DIRECT_TEST})
    directed_env(env, [direct_item("RS-2", repo, approved_base=approved)])
    runner = Runner(plan=never_plans, agent=None, generate=no_model)
    step(env, runner)
    harvested = step(env, runner)
    assert harvested["worker_status"] == "REVIEW_REQUIRED" and "NEEDS_REVALIDATION" in harvested["worker_summary"]
    assert backlog_mod.ownership(*_live(env))[0]["state"] == "NEEDS_REVALIDATION"


def test_a_file_under_another_open_pull_request_blocks_the_slice_without_using_its_attempt(env):
    directed_env(env, [direct_item("RS-2", REPOS[1])])
    runner = Runner(plan=never_plans, agent=None, generate=no_model)
    blocked = step(env, runner, owned=lambda repo: ["pkg/core.py"])
    assert blocked["status"] == "BLOCKED_CONFLICT" and blocked["files"] == ["pkg/core.py"] and runner.spawned == 0
    items, state = _live(env)
    assert state["items"]["RS-2"]["attempts"] == 0 and backlog_mod.ownership(items, state)[0]["state"] == (
        "BLOCKED_CONFLICT")
    assert step(env, Runner(plan=never_plans, agent=None, generate=replying("x")))["backlog_item"] == "RS-2"


@pytest.mark.parametrize(("change", "row", "expected"), [
    ({"status": "proposed"}, {}, "PROPOSED"),
    ({"status": "retired", "superseded_by": "Y"}, {}, "SUPERSEDED"),
    ({"assignee": "claude-code"}, {}, "ASSIGNED_ELSEWHERE"),
    ({}, {"outcome": "FAIL: DIRECTED_REPLY_REJECTED: UNEXPECTED_UNIT: x"}, "FAILED_IMPLEMENTATION"),
    ({}, {"outcome": "DISPATCHED"}, "IN_FLIGHT"),
    ({"depends_on": ["Z"]}, {}, "BLOCKED_DEPENDENCY"),
    ({"status": "done"}, {}, "COMPLETED"),
    ({}, {}, "READY"),
])
def test_every_semantic_slice_state_is_derived_from_the_backlog_and_the_lane_record(change, row, expected):
    item = {**direct_item("X", REPOS[0]), **change}
    assert backlog_mod.slice_state(item, row, done=set(), conflicting=[]) == expected


def test_an_approved_level_2_item_needs_a_complete_direction():
    item = direct_item("X", REPOS[0])
    assert backlog_mod.item_problems(item) == []
    del item["direct"]["test_source"]
    assert "APPROVED_ITEM_WITHOUT_DIRECTION" in backlog_mod.item_problems(item)


# Merge readiness and automatic review repair (owner decision 2026-10-30: automatic merging is a required
# capability, but only through the established review requirements).

REPAIRED_MODULE = ('def add_two(value: int) -> int:\n    """Return value plus two (negative input reviewed)."""\n'
                   '    return value + 2\n')


def build_then_repair_agent(worktree, message, files, check):
    """Builds the slice, and on a review repair edits only the module named by the findings."""
    check()
    assert files == ["pkg/extra.py"]
    if "Code review of pull request" in message:
        assert "Fix only pkg/extra.py" in message and "P1 incorrect result" in message
        (worktree / "pkg" / "extra.py").write_text(REPAIRED_MODULE, encoding="utf-8")
    else:
        good_agent(worktree, message, files, check)


def published(env, runner, github):
    """dispatch -> accepted -> pull request opened; returns (publication id, item, PR head)."""
    assert step(env, runner, github)["status"] == "DISPATCHED"
    assert step(env, runner, github)["status"] == "ACCEPTED_LOCAL"
    step(env, runner, github, allow=False)
    (packet_id, item), = pl.ProductLane(env.state).publications()["items"].items()
    return packet_id, item, item["head_sha"]


def only_item(env):
    return next(iter(pl.ProductLane(env.state).publications()["items"].values()))


def test_a_green_pull_request_with_an_unresolved_blocking_finding_never_merges(env):
    runner, github = Runner(), FakeGitHub(("pass",))
    packet_id, _item, head = published(env, runner, github)
    github.threads = [review_thread("T1", head)]
    step(env, runner, github, allow=False)
    item = only_item(env)
    assert github.merged == [] and item["state"] == "REPAIR_PENDING"
    assert item["findings"][0]["thread_id"] == "T1" and item["findings"][0]["blocking"] is True
    record = json.loads((pl.ProductLane(env.state).reports / f"{packet_id}-review-findings-1.json").read_text())
    assert record["head_sha"] == head and record["module_path"] == "pkg/extra.py"
    assert record["pr"]["number"] == 1 and record["findings"][0]["path"] == "pkg/extra.py"


def test_incomplete_required_ci_never_merges(env):
    runner, github = Runner(), FakeGitHub(("pass", "pending"))
    published(env, runner, github)
    step(env, runner, github, allow=False)
    assert github.merged == [] and only_item(env)["state"] == "AWAITING_CI"
    merger = readiness.load_merger(ROOT)
    pr = {"headRefOid": "h", "baseRefName": "main", "state": "OPEN", "mergeStateStatus": "CLEAN",
          "author": {"login": "jweter"}, "statusCheckRollup": [{"status": "IN_PROGRESS", "conclusion": ""}]}
    gate = readiness.assess(pr, [], verified_head="h", verification_ok=True, settled=True, age_seconds=9999,
                            repaired_thread_ids=set(), merger=merger)
    assert (gate.status, gate.reason) == (readiness.WAIT, "CHECKS_PENDING")


def test_a_new_unverified_commit_invalidates_the_verification(env):
    runner, github = Runner(), FakeGitHub(("pass",))
    _packet_id, item, _head = published(env, runner, github)
    github.heads[item["branch"]] = "e" * 40  # someone else pushed to the pull request after Iron God verified it
    step(env, runner, github, allow=False)
    blocked = only_item(env)
    assert github.merged == [] and blocked["state"] == "BLOCKED" and blocked["reason"] == "STALE_VERIFIED_HEAD"


def test_review_has_to_settle_and_github_has_to_report_mergeable_before_merging(env):
    runner, github = Runner(), FakeGitHub(("pass",), settled=False, merge_state="UNKNOWN")
    published(env, runner, github)
    step(env, runner, github, allow=False)
    assert github.merged == [] and only_item(env)["readiness"]["reason"] == "REVIEW_SETTLE_WINDOW"
    github.settled = True
    step(env, runner, github, allow=False)
    assert github.merged == [] and only_item(env)["readiness"]["reason"] == "MERGE_STATE_UNKNOWN"
    github.merge_state = "CLEAN"
    step(env, runner, github, allow=False)
    assert len(github.merged) == 1


def test_a_properly_verified_pull_request_merges_automatically(env):
    runner, github = Runner(), FakeGitHub(("pass",))
    _packet_id, _item, head = published(env, runner, github)
    step(env, runner, github, allow=False)
    item = only_item(env)
    assert github.merged == [(item["repository"], 1, head)] and item["state"] == "MERGED"
    assert item["readiness"]["status"] == readiness.MERGE


def test_a_repaired_pull_request_is_reverified_and_then_merges(env):
    backlog_env(env, [backlog_item("RS-1", REPOS[1])])
    runner, github = Runner(plan=never_plans, agent=build_then_repair_agent), FakeGitHub(("pass",))
    packet_id, _item, head = published(env, runner, github)
    github.threads = [review_thread("T1", head)]
    step(env, runner, github, allow=False)  # gate: finding on the current head -> repair queued
    assert only_item(env)["state"] == "REPAIR_PENDING"
    repair = step(env, runner, github)  # the bounded repair is dispatched before any new work
    assert repair["status"] == "DISPATCHED" and repair["repair_of"] == packet_id and repair["base_sha"] == head
    assert step(env, runner, github)["status"] == "REPAIR_ACCEPTED"
    repaired = only_item(env)
    assert repaired["state"] == "PUBLISH_PENDING" and repaired["head_sha"] != head
    assert repaired["verified_by"].startswith(f"{packet_id}-R1-") and repaired["repaired_threads"] == ["T1"]
    step(env, runner, github, allow=False)  # fast-forward push to the same pull request; fresh CI
    assert len(github.opened) == 1 and github.pushed[-1] == (repaired["branch"], repaired["head_sha"])
    git(env.remotes[REPOS[1]], "merge-base", "--is-ancestor", head, repaired["head_sha"])  # raises if not FF
    step(env, runner, github, allow=False)  # verified, settled, finding-free head: repaired thread resolved
    assert github.resolved == ["T1"] and github.merged == []
    step(env, runner, github, allow=False)  # GitHub now reports CLEAN -> merge of the repaired head
    assert github.merged == [(REPOS[1], 1, repaired["head_sha"])] and only_item(env)["state"] == "MERGED"
    assert backlog_mod.load_state(pl.ProductLane(env.state).root)["items"]["RS-1"]["outcome"] == "MERGED"
    assert runner.spawned == 2


def test_an_unrepairable_finding_is_bounded_and_blocks_with_its_evidence(env):
    backlog_env(env, [backlog_item("RS-1", REPOS[1])])

    def repair_changes_nothing(worktree, message, files, check):
        if "Code review of pull request" not in message:
            good_agent(worktree, message, files, check)

    runner, github = Runner(plan=never_plans, agent=repair_changes_nothing), FakeGitHub(("pass",))
    _packet_id, _item, head = published(env, runner, github)
    github.threads = [review_thread("T1", head)]
    step(env, runner, github, allow=False)
    for _attempt in range(pl.MAX_REVIEW_REPAIRS):
        assert step(env, runner, github)["status"] == "DISPATCHED"
        assert step(env, runner, github)["status"] != "REPAIR_ACCEPTED"
    item = only_item(env)
    assert item["state"] == "BLOCKED" and item["reason"].startswith("REVIEW_REPAIR_EXHAUSTED")
    assert "REVIEW_REPAIR_PRODUCED_NO_CHANGE" in item["reason"] and github.merged == []
    assert backlog_mod.load_state(pl.ProductLane(env.state).root)["items"]["RS-1"]["outcome"] == "PR_BLOCKED"
    assert step(env, runner, github)["status"] == "IDLE_NO_APPROVED_BACKLOG_ITEM"
    assert runner.spawned == 1 + pl.MAX_REVIEW_REPAIRS  # never an unbounded loop


def test_a_finding_outside_the_slice_module_is_not_repaired_blindly(env):
    runner, github = Runner(), FakeGitHub(("pass",))
    _packet_id, _item, head = published(env, runner, github)
    github.threads = [review_thread("T1", head, path="tests/test_extra.py")]
    step(env, runner, github, allow=False)
    item = only_item(env)
    assert item["state"] == "BLOCKED" and item["reason"].startswith("REVIEW_FINDING_OUT_OF_REPAIR_SCOPE")
    assert runner.spawned == 1 and github.merged == []


def test_a_merge_failure_is_recorded_without_corrupting_the_backlog(env):
    backlog_env(env, [backlog_item("RS-1", REPOS[1])])
    runner, github = Runner(plan=never_plans), FakeGitHub(("pass",), fail_merge=True)
    published(env, runner, github)
    step(env, runner, github, allow=False)
    item = only_item(env)
    assert item["state"] == "BLOCKED" and "MERGE_FAILED" in item["reason"]
    state = backlog_mod.load_state(pl.ProductLane(env.state).root)  # still loads: not corrupted
    assert state["items"]["RS-1"]["outcome"] == "PR_BLOCKED"
    assert wm.load(env.state)["repositories"][REPOS[1]]["blockers"]


def test_a_successful_merge_is_recorded_as_a_completed_delivery(env):
    backlog_env(env, [backlog_item("RS-1", REPOS[1])])
    runner, github = Runner(plan=never_plans), FakeGitHub(("pass",))
    published(env, runner, github)
    step(env, runner, github, allow=False)
    assert only_item(env)["state"] == "MERGED" and only_item(env)["merged_sha"] == "f" * 40
    assert backlog_mod.load_state(pl.ProductLane(env.state).root)["items"]["RS-1"]["outcome"] == "MERGED"
    lesson = load_shared_memory(env.memory)["entries"][-1]
    assert lesson["evidence_state"] == "VERIFIED" and lesson["repository"] == REPOS[1]
    assert wm.load(env.state)["repositories"][REPOS[1]]["last_outcome"] == "MERGED"

def test_a_reviewer_reopening_a_repaired_finding_on_the_new_head_gets_another_repair_not_a_resolve(env):
    backlog_env(env, [backlog_item("RS-1", REPOS[1])])
    runner, github = Runner(plan=never_plans, agent=build_then_repair_agent), FakeGitHub(("pass",))
    _packet_id, _item, head = published(env, runner, github)
    github.threads = [review_thread("T1", head)]
    step(env, runner, github, allow=False)
    step(env, runner, github)
    assert step(env, runner, github)["status"] == "REPAIR_ACCEPTED"
    step(env, runner, github, allow=False)  # repaired head pushed to the same pull request
    repaired_head = only_item(env)["head_sha"]
    github.threads[0]["comments"].append({"body": "P1 still incorrect for negative input", "commit": repaired_head})
    step(env, runner, github, allow=False)
    item = only_item(env)
    assert github.resolved == [] and github.merged == []  # never resolved over a live current-head finding
    assert item["state"] == "REPAIR_PENDING" and item["readiness"]["reason"] == "REVIEW_FINDINGS_ON_CURRENT_HEAD"



def test_owner_takeover_during_a_repair_requeues_it_without_spending_an_attempt(env):
    """PR #588 review: a cancelled repair must not strand the pull request in REPAIRING."""
    backlog_env(env, [backlog_item("RS-1", REPOS[1])])
    runner, github = Runner(plan=never_plans, agent=build_then_repair_agent), FakeGitHub(("pass",))
    _packet_id, _item, head = published(env, runner, github)
    github.threads = [review_thread("T1", head)]
    step(env, runner, github, allow=False)
    assert step(env, runner, github)["status"] == "DISPATCHED"
    assert only_item(env)["state"] == "REPAIRING"
    assert step(env, runner, github, decision=DENY)["status"] == "WORKER_STOPPED_AUTHORITY_REVOKED"
    item = only_item(env)
    assert item["state"] == "REPAIR_PENDING" and item["repair_attempts"] == 0
    again = step(env, runner, github)  # authority back: the repair runs again, the repository is not stranded
    assert again["status"] == "DISPATCHED" and again["repair_of"] == _packet_id
    assert step(env, runner, github)["status"] == "REPAIR_ACCEPTED"


def test_a_repair_whose_worker_vanished_is_reconciled_not_stranded(env):
    runner, github = Runner(), FakeGitHub(("pass",))
    packet_id, _item, _head = published(env, runner, github)
    lane = pl.ProductLane(env.state)
    items = lane.publications()["items"]
    items[packet_id].update(state="REPAIRING", repair_attempts=1, repair_module="pkg/extra.py",  # crashed mid-repair
                            repair_test="tests/test_extra.py", findings=[{"thread_id": "T1", "path": "pkg/extra.py"}])
    pl._save_publications(lane, items)
    step(env, runner, github)
    assert only_item(env)["state"] in {"REPAIR_PENDING", "REPAIRING"}  # requeued (then possibly re-dispatched)


def test_a_reviewer_follow_up_on_the_current_head_reaches_the_repair_worker(env):
    """PR #588 review: the repair input must carry the current-head follow-up, not only the first comment."""
    backlog_env(env, [backlog_item("RS-1", REPOS[1])])
    messages = []

    def recording_agent(worktree, message, files, check):
        messages.append(message)
        build_then_repair_agent(worktree, message, files, check)

    runner, github = Runner(plan=never_plans, agent=recording_agent), FakeGitHub(("pass",))
    _packet_id, _item, head = published(env, runner, github)
    github.threads = [review_thread("T1", head)]
    step(env, runner, github, allow=False)
    step(env, runner, github)
    assert step(env, runner, github)["status"] == "REPAIR_ACCEPTED"
    step(env, runner, github, allow=False)
    repaired_head = only_item(env)["head_sha"]
    github.threads[0]["comments"].append({"body": "Still wrong: -2 must return 0, see the boundary case",
                                          "commit": repaired_head})
    step(env, runner, github, allow=False)
    finding = only_item(env)["findings"][0]
    assert [c["on_current_head"] for c in finding["comments"]] == [False, True]
    step(env, runner, github)  # second repair dispatched
    assert "Still wrong: -2 must return 0" in messages[-1] and "on the current head" in messages[-1]
    assert "P1 incorrect result" in messages[-1]


def test_reviewer_followup_arriving_just_before_thread_resolution_is_not_silenced(env):
    """A new current-head review between snapshots must cause repair, not auto-resolution."""
    class LateReviewer(FakeGitHub):
        def __init__(self):
            super().__init__(("pass",))
            self.thread_reads = 0

        def review_threads(self, repo, number):
            self.thread_reads += 1
            if self.thread_reads == 2:
                head = self.heads[self.branches[number]]
                self.threads[0]["comments"].append({
                    "body": "P1: current-head correction arrived after readiness snapshot",
                    "commit": head,
                })
            return super().review_threads(repo, number)

    runner, github = Runner(), LateReviewer()
    packet_id, _item, head = published(env, runner, github)
    github.threads = [review_thread("T1", "b" * 40)]
    lane = pl.ProductLane(env.state)
    items = lane.publications()["items"]
    items[packet_id]["repaired_threads"] = ["T1"]
    pl._save_publications(lane, items)

    step(env, runner, github, allow=False)
    item = only_item(env)
    assert item["state"] == "REPAIR_PENDING"
    assert item["readiness"]["reason"] == "REVIEW_FINDINGS_ON_CURRENT_HEAD"
    assert item["findings"][0]["comments"][-1]["commit"] == head
    assert github.resolved == [] and github.merged == []


def test_current_head_review_added_between_merge_readiness_and_merge_queues_repair(env):
    """A late blocking comment cannot be bypassed by an earlier CLEAN PR snapshot."""
    class LateReviewer(FakeGitHub):
        def __init__(self):
            super().__init__(("pass",))
            self.thread_reads = 0

        def review_threads(self, repo, number):
            self.thread_reads += 1
            if self.thread_reads == 2:
                head = self.heads[self.branches[number]]
                self.threads.append(review_thread("T-LATE", head))
            return super().review_threads(repo, number)

    runner, github = Runner(), LateReviewer()
    published(env, runner, github)
    step(env, runner, github, allow=False)
    item = only_item(env)
    assert github.thread_reads == 2
    assert github.merged == [] and github.resolved == []
    assert item["state"] == "REPAIR_PENDING"
    assert item["readiness"]["reason"] == "REVIEW_FINDINGS_ON_CURRENT_HEAD"
    assert item["findings"][0]["thread_id"] == "T-LATE"


def write_calibration(env):
    """The owner-held calibration profile the routing step reads, bound to this model and hardware."""
    from project_orchestrator import iron_god_calibration as cal

    cal.profile_path(env.state).parent.mkdir(parents=True, exist_ok=True)
    cal.profile_path(env.state).write_text(json.dumps({
        "schema_version": 1, "ladder_version": cal.LADDER_VERSION, "model": cal.DEFAULT_MODEL,
        "hardware": cal.hardware_fingerprint(), "max_passing_level": 3, "levels": {}}), encoding="utf-8")


def publish_graph(env, repo):
    """Commit a declared capability graph to the repository's 'GitHub' main, as an owner roadmap PR would."""
    work = env.tmp / "graph-work" / repo.split("/")[1]
    git(env.tmp, "clone", "-q", str(env.remotes[repo]), str(work))
    git(work, "config", "user.email", "o@example.com")
    git(work, "config", "user.name", "Owner")
    source = "engineering/capability-graph.json"
    payload = {"schema_version": 1, "repository": repo, "roadmap": ["cap-extra"], "capabilities": [{
        "id": "cap-extra", "capability": "Extra helpers", "state": "PLANNED", "depends_on": [],
        "completion_evidence": ["docs/ROADMAP.md extras"],
        "slices": [{"id": "extra-helper", "state": "PLANNED", "capability_level": 1, "objective": "Add add_two for the export workflow.",
                    "acceptance": ["examples hold"], "allowed_paths": ["pkg/**", "tests/**"]}]}]}
    (work / source).write_text(json.dumps(payload), encoding="utf-8")
    (work / "engineering" / "control-plane.json").write_text(
        json.dumps({"schema_version": 1, "capability_graph": source, "authoritative_documents": [source]}), encoding="utf-8")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "owner roadmap graph")
    git(work, "push", "-q", "origin", "main")


def test_an_empty_backlog_is_refilled_from_the_roadmap_and_the_item_merges_through_the_normal_gates(env):
    from project_orchestrator import iron_god_roadmap_refill as refill_mod

    backlog_env(env, [])
    # this test supplies the draft by hand (as an external planner would), so the local drafter stays out of it
    env.policy = {**env.policy, "portfolio_rotation": {**env.policy["portfolio_rotation"], "local_draft_author": False}}
    repo = REPOS[1]
    publish_graph(env, repo)
    write_calibration(env)
    runner, github = Runner(plan=never_plans), FakeGitHub(("pass",))
    lane = pl.ProductLane(env.state)

    idle = step(env, runner, github)  # empty approved backlog: planning runs, a request is written, nothing is invented
    assert idle["status"] == "IDLE_NO_APPROVED_BACKLOG_ITEM" and runner.spawned == 0
    request = json.loads((lane.root / refill_mod.REQUESTS / (refill_mod.item_id(repo, "cap-extra", "extra-helper") + ".json")).read_text("utf-8"))
    assert request["roadmap_ref"] == "engineering/capability-graph.json#cap-extra/extra-helper"
    assert request["exact_head"] == git(env.tmp / "graph-work" / "VoiceStudio", "rev-parse", "HEAD")

    draft = backlog_item(request["id"], repo)  # a qualified planner answers the request
    draft.update(roadmap_ref=request["roadmap_ref"], addresses="cap-extra: extra helpers", status="proposed")
    (lane.root / refill_mod.DRAFTS).mkdir(parents=True, exist_ok=True)
    (lane.root / refill_mod.DRAFTS / f"{request['id']}.json").write_text(json.dumps(draft), encoding="utf-8")

    dispatched = step(env, runner, github)
    assert dispatched["status"] == "DISPATCHED" and dispatched["backlog_item"] == request["id"], dispatched
    assert step(env, runner, github)["status"] == "ACCEPTED_LOCAL"
    step(env, runner, github, allow=False)  # push + PR
    assert github.opened[0]["title"].startswith(f"Iron God [{request['id']}]")
    assert "Roadmap: engineering/capability-graph.json#cap-extra/extra-helper" in github.opened[0]["body"]
    step(env, runner, github, allow=False)  # CI green -> merge
    assert backlog_mod.load_state(lane.root)["items"][request["id"]]["outcome"] == "MERGED"
    ledger = refill_mod.load_store(lane.root)["ledger"]
    assert [r["kind"] for r in ledger if r["repository"] == repo] == ["GENERATED", "ADMITTED"]
    undeclared = {r["repository"] for r in ledger if r["kind"] == "DIRECTION_REQUIRED"}
    assert undeclared == {REPOS[0], REPOS[2]}  # no declared roadmap authority: a focused question, never a guess
    assert "ROADMAP_REFILL" in json.dumps(wm.load(env.state))  # the next planner reads it from shared context


def test_roadmap_refill_failures_never_stop_the_rotation_or_invent_work(env):
    backlog_env(env, [backlog_item("RS-1", REPOS[1])])
    publish_graph(env, REPOS[2])

    real_sync = env.sync

    def flaky_sync(lane, repo):
        if repo == REPOS[0]:
            raise RuntimeError("PRODUCT_FETCH_FAILED")
        return real_sync(lane, repo)

    env.sync = flaky_sync
    runner = Runner(plan=never_plans)
    result = step(env, runner, FakeGitHub(("pass",)))

    assert result["status"] == "DISPATCHED" and result["backlog_item"] == "RS-1"
    assert "ROADMAP_REFILL_FAILED" in json.dumps(wm.load(env.state))


def test_a_repository_with_an_open_publication_is_not_replanned(env):
    backlog_env(env, [])
    publish_graph(env, REPOS[1])
    lane = pl.ProductLane(env.state)
    busy = {REPOS[1]}
    approved = pl.refill_from_roadmaps(lane, REPOS, busy, [], {}, 2, env.sync, lambda repo: [])

    from project_orchestrator import iron_god_roadmap_refill as refill_mod
    requests = lane.root / refill_mod.REQUESTS
    assert approved == [] and not list(requests.glob("*.json") if requests.is_dir() else [])  # in-flight repo: untouched


def test_a_repository_without_a_python_package_is_unsupported_for_the_local_builder(tmp_path):
    cpp, python = tmp_path / "cpp", tmp_path / "python"
    for root, files in ((cpp, {"Source/Game.cpp": "int main() { return 0; }\n", "Config/Default.ini": "[a]\n"}),
                        (python, {"pkg/__init__.py": "", "pkg/core.py": "def one():\n    return 1\n"})):
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        git(tmp_path, "init", "-q", "-b", "main", str(root))
        git(root, "config", "user.email", "o@example.com")
        git(root, "config", "user.name", "Owner")
        git(root, "add", ".")
        git(root, "commit", "-q", "-m", "seed")

    assert pl.repository_unsupported(cpp, git(cpp, "rev-parse", "HEAD")) == "NO_PYTHON_PACKAGE_FOR_LOCAL_BUILDER"
    assert pl.repository_unsupported(python, git(python, "rev-parse", "HEAD")) is None


def test_capable_requests_are_logged_and_never_fire_unless_enabled_and_configured(env, monkeypatch):
    from project_orchestrator import iron_god_capable_builder as cap
    from project_orchestrator import iron_god_roadmap_refill as refill_mod

    lane = pl.ProductLane(env.state)
    fired = []
    monkeypatch.setattr(cap.urllib.request, "urlopen", lambda *a, **k: fired.append(a) or (_ for _ in ()).throw(AssertionError))
    monkeypatch.delenv(cap.ROUTINES[cap.ROUTINE][1], raising=False)
    request = {"id": "RM-x", "repository": REPOS[1], "exact_head": "a" * 40, "roadmap_ref": "g#c/s", "objective": "o",
               "allowed_paths": ["pkg/**"], "forbidden_paths": [], "route": {"builder": "CAPABLE", "reason": "X"}}
    report = {"capable_requests": [request]}

    pl.hand_to_capable_builder(lane, REPOS[1], report, {"enabled": False, "daily_limit": 2})
    pl.hand_to_capable_builder(lane, REPOS[1], report, {"enabled": True, "daily_limit": 2})

    ledger = [(e["kind"], e["detail"]["reason"]) for e in refill_mod.load_store(lane.root)["ledger"]]
    assert ledger == [("CAPABLE_NOT_DISPATCHED", "CAPABLE_BUILDER_DISABLED"),
                      ("CAPABLE_NOT_DISPATCHED", "CAPABLE_BUILDER_UNCONFIGURED")]
    assert fired == [] and "CAPABLE_NOT_DISPATCHED" in json.dumps(wm.load(env.state))


def test_a_routine_api_error_is_a_recorded_blocker_not_a_crash(env, monkeypatch):
    from project_orchestrator import iron_god_capable_builder as cap
    from project_orchestrator import iron_god_roadmap_refill as refill_mod

    lane = pl.ProductLane(env.state)

    def explode(*_a, **_k):
        raise OSError("network down")

    monkeypatch.setattr(cap, "dispatch_request", explode)
    request = {"id": "RM-y", "repository": REPOS[1], "route": {"builder": "CAPABLE"}}

    pl.hand_to_capable_builder(lane, REPOS[1], {"capable_requests": [request]}, {"enabled": True, "daily_limit": 2})

    (row,) = refill_mod.load_store(lane.root)["ledger"]
    assert row["kind"] == "CAPABLE_NOT_DISPATCHED" and "CAPABLE_BUILDER_ERROR: OSError" in row["detail"]["reason"]


def test_the_shipped_policy_keeps_the_capable_builder_off():
    assert POLICY["portfolio_rotation"]["capable_builder"] is False
    assert POLICY["portfolio_rotation"]["capable_builder_daily_limit"] == 2


def test_roadmap_progress_outcomes_are_logged_and_errors_never_stop_the_rotation(env, monkeypatch):
    from project_orchestrator import iron_god_roadmap_progress as progress_mod
    from project_orchestrator import iron_god_roadmap_refill as refill_mod

    lane = pl.ProductLane(env.state)
    outcomes = iter([{"status": "OPENED", "branch": "iron-god/roadmap-progress/x-1", "pr": "https://github.com/o/r/pull/5"},
                     {"status": "NOTHING_TO_RECORD"},
                     {"status": "BLOCKED", "reason": "GRAPH_FORMAT_NOT_CANONICAL"}])

    def fake(*_a, **_k):
        value = next(outcomes, None)
        if value is None:
            raise OSError("git unavailable")
        return value

    monkeypatch.setattr(progress_mod, "record_progress", fake)
    for _ in range(4):
        pl.record_roadmap_progress(lane, REPOS[1], env.tmp, "a" * 40, [], {}, (env.release, "a" * 40))

    kinds = [e["kind"] for e in refill_mod.load_store(lane.root)["ledger"]]
    assert kinds == ["PROGRESS_OPENED", "PROGRESS_BLOCKED", "PROGRESS_ERROR"]  # NOTHING_TO_RECORD is silent
    assert "ROADMAP_PROGRESS_OPENED" in json.dumps(wm.load(env.state))


def test_the_shipped_policy_records_roadmap_progress():
    assert POLICY["portfolio_rotation"]["roadmap_progress"] is True


def test_progress_is_not_started_without_a_release_identity_and_waits_for_an_idle_lane(env, monkeypatch):
    from project_orchestrator import iron_god_roadmap_progress as progress_mod

    lane = pl.ProductLane(env.state)
    seen = []

    def fake(*_a, **kwargs):
        seen.append(kwargs["may_start"])
        kwargs["spawn"](env.tmp / "tree", env.tmp / "result.json")
        return {"status": "NOTHING_TO_RECORD"}

    launched = []
    monkeypatch.setattr(progress_mod, "record_progress", fake)
    monkeypatch.setattr(pl, "spawn_progress_preflight", lambda *a: launched.append(a) or 77)

    pl.record_roadmap_progress(lane, REPOS[1], env.tmp, "a" * 40, [], {}, None)  # no release identity: nothing runs
    assert seen == []
    pl.record_roadmap_progress(lane, REPOS[1], env.tmp, "a" * 40, [], {}, (env.release, "b" * 40))
    lane.active_path.parent.mkdir(parents=True, exist_ok=True)
    lane.active_path.write_text(json.dumps({"packet_id": "P-1"}), encoding="utf-8")
    pl.record_roadmap_progress(lane, REPOS[1], env.tmp, "a" * 40, [], {}, (env.release, "b" * 40))

    assert seen == [True, False]  # idle lane may start verification; an active worker keeps it waiting
    assert launched[0][0] == env.release and launched[0][1] == "b" * 40  # bound to the release identity


def test_no_builder_is_dispatched_while_the_local_model_is_drafting_a_roadmap_slice(env):
    import time as time_mod

    from project_orchestrator import iron_god_local_drafter as drafter_mod

    backlog_env(env, [backlog_item("RS-1", REPOS[1])])
    lane = pl.ProductLane(env.state)
    job = lane.root / drafter_mod.JOBS / "RM-x.a0.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"id": "RM-x", "attempt": 0, "stage": "DRAFTING", "started_at": time_mod.time(),
                               "result": str(lane.root / "never.json")}), encoding="utf-8")
    runner = Runner(plan=never_plans)

    held = step(env, runner, FakeGitHub(("pass",)))

    assert held["status"] == "WAITING_FOR_LOCAL_DRAFT" and runner.spawned == 0
    job.unlink()
    assert step(env, runner, FakeGitHub(("pass",)))["status"] == "DISPATCHED"  # released as soon as the job is gone


def test_local_draft_outcomes_are_logged_and_a_drafter_error_never_stops_the_rotation(env, monkeypatch):
    from project_orchestrator import iron_god_local_drafter as drafter_mod
    from project_orchestrator import iron_god_roadmap_refill as refill_mod

    lane = pl.ProductLane(env.state)
    calls = []

    def fake(root, requests, clone, head, **kwargs):
        calls.append(kwargs["may_start"])
        if len(calls) == 3:
            raise OSError("ollama gone")
        return [{"id": "RM-a", "status": "STARTED", "attempt": 0}, {"id": "RM-b", "status": "FAILED", "reason": "PLAN_PATH_INVALID"}]

    monkeypatch.setattr(drafter_mod, "advance", fake)
    report = {"local_requests": []}
    pl.draft_with_local_builder(lane, REPOS[1], report, env.tmp, "a" * 40, None)  # no release identity: nothing runs
    assert calls == []
    pl.draft_with_local_builder(lane, REPOS[1], report, env.tmp, "a" * 40, (env.release, "b" * 40))
    pl.draft_with_local_builder(lane, REPOS[1], report, env.tmp, "a" * 40, (env.release, "b" * 40))
    pl.draft_with_local_builder(lane, REPOS[1], report, env.tmp, "a" * 40, (env.release, "b" * 40))

    kinds = [e["kind"] for e in refill_mod.load_store(lane.root)["ledger"]]
    assert kinds == ["LOCAL_DRAFT_STARTED", "LOCAL_DRAFT_FAILED"] * 2 + ["LOCAL_DRAFT_ERROR"]
    assert "LOCAL_DRAFT_FAILED" in json.dumps(wm.load(env.state))


def test_the_shipped_policy_enables_the_local_draft_author():
    assert POLICY["portfolio_rotation"]["local_draft_author"] is True


def test_a_draft_job_holds_the_runtime_lease_so_a_repair_or_build_cannot_run_beside_it(env, monkeypatch):
    """Review P2 on #608: a repair acquired its normal lease and ran inference alongside the draft."""
    from project_orchestrator import iron_god_local_drafter as drafter_mod

    lane = pl.ProductLane(env.state)
    examples = [{"call": "{fn}([1, 2, 3, 4], 2)", "expected": [3, 4], "why": "the newest two"},
                {"call": "{fn}([1, 2, 3], 0)", "expected": [], "why": "nothing kept"}]
    request = {"id": "RM-x", "repository": REPOS[1], "slice": "s1", "capability": "c", "roadmap_ref": "g#c/s",
               "objective": "Keep the newest entries.", "allowed_paths": ["pkg/**"], "forbidden_paths": [],
               "slice_acceptance": [], "capability_level": 1, "acceptance_examples": examples,
               "route": {"builder": "LOCAL"}}
    monkeypatch.setattr(pl, "spawn_draft_author", lambda *a: 4242)

    pl.draft_with_local_builder(lane, REPOS[1], {"local_requests": [request]}, env.tmp, "a" * 40,
                                (env.release, "b" * 40), env.pool)

    assert drafter_mod.drafting_in_progress(lane.root)
    with pytest.raises(Exception):
        env.pool.acquire(pl.WORKER_LEASE_RUNTIME, pl.WORKER_LEASE_ID, owner_pid=os.getpid())
    (job,) = drafter_mod._jobs(lane.root)
    Path(job["result"]).parent.mkdir(parents=True, exist_ok=True)
    Path(job["result"]).write_text(json.dumps({"ok": False, "reason": "PlanRejected: PLAN_PATH_INVALID"}), encoding="utf-8")
    pl.refill_from_roadmaps(lane, [], set(), [], {}, 2, env.sync, lambda repo: [], draft_locally=True, lease_pool=env.pool)

    assert not drafter_mod.drafting_in_progress(lane.root)  # harvested every turn, whichever repository is refilled
    lease = env.pool.acquire(pl.WORKER_LEASE_RUNTIME, pl.WORKER_LEASE_ID, owner_pid=os.getpid())
    env.pool.release(pl.WORKER_LEASE_ID, lease.lease_id)  # the lease came back


def test_a_standing_missing_examples_question_is_asked_once(env, monkeypatch):
    from project_orchestrator import iron_god_roadmap_refill as refill_mod

    lane = pl.ProductLane(env.state)
    request = {"id": "RM-y", "repository": REPOS[1], "slice": "s1", "capability": "c", "objective": "o",
               "capability_level": 1, "acceptance_examples": [], "route": {"builder": "LOCAL"}}
    monkeypatch.setattr(pl, "spawn_draft_author", lambda *a: 1)

    for _ in range(3):
        pl.draft_with_local_builder(lane, REPOS[1], {"local_requests": [request]}, env.tmp, "a" * 40,
                                    (env.release, "b" * 40), env.pool)

    kinds = [e["kind"] for e in refill_mod.load_store(lane.root)["ledger"]]
    assert kinds == ["LOCAL_DRAFT_NEEDS_ACCEPTANCE_EXAMPLES"]


# --- the whole roadmap loop, end to end, across repositories and restarts (rehearsal for the release procedure) -------


def publish_graph_payload(env, repo, payload):
    """Commit a declared capability graph to the repository's 'GitHub' main, as an owner roadmap PR would."""
    work = env.tmp / "graph-work2" / repo.split("/")[1]
    git(env.tmp, "clone", "-q", str(env.remotes[repo]), str(work))
    git(work, "config", "user.email", "o@example.com")
    git(work, "config", "user.name", "Owner")
    source = "engineering/capability-graph.json"
    (work / source).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    (work / "engineering" / "control-plane.json").write_text(
        json.dumps({"schema_version": 1, "capability_graph": source, "authoritative_documents": [source]}), encoding="utf-8")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "owner roadmap graph")
    git(work, "push", "-q", "origin", "main")


def two_step_graph(repo):
    def piece(sid, fn_word, plus, depends_on=()):
        row = {"id": sid, "state": "PLANNED", "capability_level": 1, "objective": f"Return the integer plus {fn_word}.",
               "acceptance": [f"adds {plus}"], "allowed_paths": ["pkg/**", "tests/**"],
               "acceptance_examples": [
                   {"call": "{fn}(1)", "expected": 1 + plus, "why": f"1 + {plus}"},
                   {"call": "{fn}(0)", "expected": plus, "why": f"0 + {plus}"}]}
        if depends_on:
            row["depends_on"] = list(depends_on)
        return row

    return {"schema_version": 1, "repository": repo, "roadmap": ["cap-adders"], "capabilities": [{
        "id": "cap-adders", "capability": "Adders", "state": "PLANNED", "depends_on": [], "completion_evidence": ["docs/ROADMAP.md"],
        "slices": [piece("add-two", "two", 2), piece("add-three", "three", 3, ["add-two"])]}]}


PLANS = {
    "add-two": {"function_name": "add_two", "signature": "def add_two(value: int) -> int:", "module_path": "pkg/add_two.py",
                "objective": "Return the given integer plus two, exactly.",
                "examples": [{"call": "add_two(1)", "why": "1 + 2", "expected": 3}, {"call": "add_two(0)", "why": "0 + 2", "expected": 2},
                             {"call": "add_two(-2)", "why": "-2 + 2", "expected": 0}], "note": "Added pkg.add_two."},
    "add-three": {"function_name": "add_three", "signature": "def add_three(value: int) -> int:", "module_path": "pkg/add_three.py",
                  "objective": "Return the given integer plus three, exactly.",
                  "examples": [{"call": "add_three(1)", "why": "1 + 3", "expected": 4}, {"call": "add_three(0)", "why": "0 + 3", "expected": 3},
                               {"call": "add_three(-3)", "why": "-3 + 3", "expected": 0}], "note": "Added pkg.add_three."},
}
MODULES = {"pkg/add_two.py": "def add_two(value: int) -> int:\n    \"\"\"Return value plus two.\"\"\"\n    return value + 2\n",
           "pkg/add_three.py": "def add_three(value: int) -> int:\n    \"\"\"Return value plus three.\"\"\"\n    return value + 3\n"}


def module_agent(worktree, message, files, check, source=None):
    check()
    (worktree / files[0]).write_text(MODULES[files[0]], encoding="utf-8")


def test_the_whole_roadmap_loop_runs_across_repositories_continues_after_a_merge_and_survives_restarts(env, monkeypatch):
    """Read roadmap -> next slice -> route -> draft -> admit -> build -> verify -> accept -> PR -> CI -> merge ->
    continue with the dependent slice -> record the progress, across three repositories and mid-flight restarts."""
    import functools

    from project_orchestrator import iron_god_capable_builder as cap
    from project_orchestrator import iron_god_local_drafter as drafter_mod
    from project_orchestrator import iron_god_roadmap_progress as progress_mod
    from project_orchestrator import iron_god_roadmap_refill as refill_mod
    from scripts import iron_god_draft_author as author_mod

    backlog_env(env, [])
    monkeypatch.setattr(refill_mod, "CHECK_INTERVAL_SECONDS", 0)
    main_repo, beyond_repo, undeclared_repo = REPOS[1], REPOS[0], REPOS[2]
    publish_graph_payload(env, main_repo, two_step_graph(main_repo))
    beyond = two_step_graph(beyond_repo)
    for row in beyond["capabilities"][0]["slices"]:
        row["capability_level"] = 3  # beyond what the local builder is measured to build
    publish_graph_payload(env, beyond_repo, beyond)
    write_calibration(env)
    lane = pl.ProductLane(env.state)
    monkeypatch.delenv(cap.ROUTINES[cap.ROUTINE][1], raising=False)

    # The model's plan, the builder's code and the heavy preflight process are the only stand-ins; the worker's own
    # plan validator runs for real. Each stand-in finishes at once so one lane turn sees its result on the next turn.
    def fake_draft_launch(release_repo, release_sha, request_file, clone, base, result, log_path):
        request = json.loads(Path(request_file).read_text(encoding="utf-8"))
        author_mod.run(request, clone, base, result, planner=lambda *_a: PLANS[request["slice"]],
                       validator=WORKER.validate_plan, authority=lambda: True,
                       roots=author_mod.planning_roots(request["allowed_paths"]), forbidden=[*pl.ALWAYS_FORBIDDEN],
                       attempted=request.get("attempted_functions", []))
        return 4242

    def fake_preflight_launch(release_repo, release_sha, worktree, result, log_path):
        result.parent.mkdir(parents=True, exist_ok=True)
        head = git(worktree, "rev-parse", "HEAD")
        result.write_text(json.dumps({"ok": True, "head": head, "output_tail": "28 passed"}), encoding="utf-8")
        return 4343

    published, pr_state = [], {"value": "OPEN"}
    original = progress_mod.record_progress
    monkeypatch.setattr(pl, "spawn_draft_author", fake_draft_launch)
    monkeypatch.setattr(pl, "spawn_progress_preflight", fake_preflight_launch)
    monkeypatch.setattr(progress_mod, "record_progress", functools.partial(
        original, publisher=lambda repo, branch, title, body: published.append((repo, branch, title, body)) or
        f"https://github.com/{repo}/pull/50{len(published)}", pr_lookup=lambda _repo, _branch: None,
        pr_state=lambda _url: pr_state["value"]))
    runner, github = Runner(agent=module_agent, plan=never_plans), FakeGitHub(("pass",))

    def drive(until, limit=14, **kw):
        """Run lane turns (a fresh call each time, i.e. a restart) until the predicate holds."""
        for _ in range(limit):
            result = step(env, runner, github, **kw)
            if until(result):
                return result
        raise AssertionError(f"never reached the goal; last: {result}")

    add_two = refill_mod.item_id(main_repo, "cap-adders", "add-two")
    add_three = refill_mod.item_id(main_repo, "cap-adders", "add-three")

    # 1. the first slice: planned locally from the roadmap, admitted, built and accepted (a restart between every turn)
    drive(lambda r: r["status"] == "DISPATCHED")
    state = backlog_mod.load_state(lane.root)
    assert state["items"][add_two]["outcome"] == "DISPATCHED"
    ledger = refill_mod.load_store(lane.root)["ledger"]
    assert {"GENERATED", "ADMITTED"} <= {e["kind"] for e in ledger if e["id"] == add_two}
    assert not any(e["id"] == add_three for e in ledger)  # the dependent slice waits for its dependency
    (admitted,) = refill_mod.admitted_items(lane.root)
    assert admitted["id"] == add_two and admitted["authorization"]["route"] == "LOCAL"
    assert any("roadmap acceptance example" in e["why"] for e in admitted["build"]["examples"])  # owner's examples travel on
    assert drive(lambda r: r["status"] == "ACCEPTED_LOCAL")["status"] == "ACCEPTED_LOCAL"
    drive(lambda r: bool(github.opened), allow=False)  # push + pull request
    assert github.opened[0]["title"].startswith(f"Iron God [{add_two}]")
    assert "Roadmap: engineering/capability-graph.json#cap-adders/add-two" in github.opened[0]["body"]
    drive(lambda r: backlog_mod.load_state(lane.root)["items"][add_two]["outcome"] == "MERGED", allow=False)  # CI -> merge

    # 2. continuation: the merge unlocks the dependent slice with no graph update and no human; it is built the same way
    drive(lambda r: r["status"] == "DISPATCHED" and r.get("backlog_item") == add_three)
    assert drive(lambda r: r["status"] == "ACCEPTED_LOCAL")["status"] == "ACCEPTED_LOCAL"
    drive(lambda r: len(github.opened) == 2, allow=False)
    drive(lambda r: backlog_mod.load_state(lane.root)["items"][add_three]["outcome"] == "MERGED", allow=False)
    assert len(github.merged) == 2

    # 3. durable progress: native preflight first, ONE graph pull request at a time; once it merges the graph on main
    #    shows the slices complete and nothing is proposed twice
    drive(lambda r: bool(published), limit=24)
    repo, branch, title, body = published[0]
    assert repo == main_repo and "preflight passed at this exact commit" in body and body.count("pull/") >= 1
    first_slices = {s for s in ("add-two", "add-three") if f"`{s}`" in body}
    assert "add-two" in first_slices
    step(env, runner, github)
    assert len(published) == 1  # the open progress PR keeps a second one from conflicting with it
    remote = env.remotes[main_repo]
    git(remote, "update-ref", "refs/heads/main", git(remote, "rev-parse", f"refs/heads/{branch}"))  # the PR merges
    pr_state["value"] = "MERGED"
    if first_slices != {"add-two", "add-three"}:
        drive(lambda r: len(published) == 2, limit=24)
    recorded = [s for _repo, _branch, _title, text in published for s in ("add-two", "add-three") if f"`{s}`" in text]
    assert sorted(recorded) == ["add-three", "add-two"]  # each merged slice recorded exactly once, across all PRs
    last_branch = published[-1][1]
    git(remote, "update-ref", "refs/heads/main", git(remote, "rev-parse", f"refs/heads/{last_branch}"))
    edited = json.loads(git(remote, "show", "refs/heads/main:engineering/capability-graph.json"))
    states = {s["id"]: s["state"] for s in edited["capabilities"][0]["slices"]}
    assert states == {"add-two": "VERIFIED_COMPLETE", "add-three": "VERIFIED_COMPLETE"}
    assert edited["capabilities"][0]["state"] == "PLANNED"  # a capability is never declared complete by merged slices

    # 4. nothing left to plan: the roadmap asks what comes next; it never re-plans merged work or invents a third slice
    final = step(env, runner, github)
    assert final["status"] == "IDLE_NO_APPROVED_BACKLOG_ITEM" and runner.spawned == 2
    ledger = refill_mod.load_store(lane.root)["ledger"]
    assert any(e["kind"] == "DIRECTION_REQUIRED" and e["repository"] == main_repo
               and e["detail"]["reason"] == "NO_EXECUTABLE_SLICE" for e in ledger)

    # 5. the other repositories were handled without blocking this one
    kinds = {(e["repository"], e["kind"]) for e in ledger}
    assert (beyond_repo, "CAPABILITY_BLOCKER") in kinds and (beyond_repo, "CAPABLE_NOT_DISPATCHED") in kinds
    assert (undeclared_repo, "DIRECTION_REQUIRED") in kinds
    offending = [(e["repository"], e["kind"], e["detail"]) for e in ledger
                 if e["repository"] in {beyond_repo, undeclared_repo} and e["kind"].startswith(("LOCAL_DRAFT", "ADMITTED"))]
    assert not offending, offending  # nothing beyond capability is simplified into a local build, nothing is invented
    assert not drafter_mod.drafting_in_progress(lane.root) and cap.open_request_ids(lane.root) == set()


def test_a_harvested_draft_is_logged_against_its_own_repository_not_the_one_being_refilled(env):
    from project_orchestrator import iron_god_roadmap_refill as refill_mod

    lane = pl.ProductLane(env.state)

    pl.log_draft_outcomes(lane, REPOS[2], [{"id": "RM-a", "status": "DRAFTED", "repository": REPOS[1]},
                                           {"id": "RM-b", "status": "ERROR", "reason": "OSError: x"}])

    ledger = {e["id"]: e["repository"] for e in refill_mod.load_store(lane.root)["ledger"]}
    assert ledger == {"RM-a": REPOS[1], "RM-b": REPOS[2]}  # an outcome without its own repository falls back to the pass
