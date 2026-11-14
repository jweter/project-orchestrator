"""The capable builder as an engine stage: same ledger and budget, file tools only, the engine verifies and delivers."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import time
from pathlib import Path

import pytest

from project_orchestrator import iron_god_capable_builder as cb
from project_orchestrator import iron_god_capable_engine as eng

GRAPH = "engineering/capability-graph.json"


def request(ident: str = "RM-demo-1", repository: str = "jweter/VoiceStudio") -> dict:
    return {"id": ident, "repository": repository, "exact_head": "a" * 40, "slice": "demo-slice",
            "roadmap_ref": f"{GRAPH}#cap/slice", "objective": "Add the thing.",
            "slice_acceptance": ["it works", "tests cover it"], "allowed_paths": ["backend/**", "tests/**"],
            "forbidden_paths": ["backend/secret/**"], "prior_failures": [], "development_rules": [],
            "route": {"builder": "CAPABLE", "reason": "LEVEL_BEYOND_PRODUCT_LANE"}}


def forbidden_network(*_a, **_k):
    raise AssertionError("the in-engine builder must never call the routine API")


class Lane:
    """The lane's effects, recorded: the spawned job files and the leases taken and given back."""

    def __init__(self, tmp_path: Path, *, busy: bool = False) -> None:
        self.root, self.spawned, self.released, self.busy, self.terminated = tmp_path, [], [], busy, []

    def effects(self) -> dict:
        return {"clone": self.root / "clone", "worktrees": self.root / "worktrees", "model": None,
                "reserve": lambda: None if self.busy else "lease-1", "release": self.released.append,
                "spawn": lambda job_file: self.spawned.append(job_file) or 4242}

    def go(self, req: dict | None = None, **kw) -> dict:
        kw.setdefault("enabled", True)
        kw.setdefault("daily_limit", 6)
        kw.setdefault("engine", self.effects())
        return cb.dispatch_request(req or request(), lane_root=self.root, transport=cb.ENGINE_TRANSPORT,
                                   opener=forbidden_network, which=lambda _name: "C:/bin/claude.exe", environ={}, **kw)

    def rows(self) -> list[dict]:
        return cb._rows(cb.dispatch_state(self.root))

    def finish(self, row: dict, result: dict) -> None:
        Path(row["result"]).write_text(json.dumps(result), encoding="utf-8")

    def reconcile(self, *, alive: bool = True, now: float | None = None, stops: bool = True) -> list[dict]:
        def unused(*_a):
            raise AssertionError("an in-engine build is never inferred from GitHub or from silence")

        def terminate(row: dict) -> bool:
            self.terminated.append(row["dispatch_id"])
            return stops
        return cb.reconcile(self.root, lookup=unused, state_of=lambda _url: None, activity=unused,
                            alive=lambda _row: alive, terminate=terminate, release=self.released.append, now=now)


# ---- the model call: file tools only ------------------------------------------------------------------------------------

def test_the_headless_call_gives_the_model_file_tools_and_nothing_else() -> None:
    argv = eng.claude_argv("claude.exe", "do it", resume="s1", model="m")
    assert argv[:3] == ("claude.exe", "-p", "do it")
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob,Edit,Write"          # no Bash: it cannot run anything
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "--strict-mcp-config" in argv and "--mcp-config" not in argv           # no MCP servers at all
    assert not any("bypass" in part.lower() or "dangerously" in part.lower() for part in argv)
    assert all(part for part in argv)                                             # the runtime refuses an empty argv item
    assert argv[argv.index("--resume") + 1] == "s1" and argv[argv.index("--model") + 1] == "m"


def test_the_in_engine_prompt_never_asks_the_model_to_use_git_or_open_a_pull_request() -> None:
    prompt = cb.render_prompt(request(), in_engine=True)
    assert "open ONE pull request" not in prompt and "Run the repository's canonical preflight" not in prompt
    assert "EDITING FILES" in prompt and "the engine commits and opens the pull request" in prompt
    assert "change NO files" in prompt                                            # how it reports a blocker
    assert "open ONE pull request" in cb.render_prompt(request())                 # the cloud routine's prompt is unchanged


# ---- the engine's own checks --------------------------------------------------------------------------------------------

def test_scope_is_the_slices_fences_plus_the_graph_and_never_workflows() -> None:
    problems = eng.scope_problems(
        ["backend/a.py", "tests/test_a.py", GRAPH, "backend/secret/key.py", "docs/x.md", ".github/workflows/ci.yml"],
        request(), GRAPH)
    assert problems == ["FORBIDDEN_PATH: .github/workflows/ci.yml", "FORBIDDEN_PATH: backend/secret/key.py",
                        "OUTSIDE_ALLOWED_PATHS: docs/x.md"]


class Builder:
    """A scripted model: each turn changes paths; the engine's preflight passes or fails per the script."""

    def __init__(self, turns: list[list[str]], preflights: list[bool]) -> None:
        self.turns, self.preflights, self.asked, self.verified, self.changed = list(turns), list(preflights), [], 0, []
        self.events: list[str] = []

    def ask(self, prompt: str, session: str | None) -> dict:
        self.asked.append((prompt, session))
        self.changed = self.turns.pop(0)
        return {"is_error": False, "session_id": "session-1", "result": "I could not tell which module was meant."}

    def commit(self) -> str:
        self.events.append("commit")
        return f"head-{self.events.count('commit')}"

    def verify(self) -> dict:
        self.events.append("verify")
        self.verified += 1
        ok = self.preflights.pop(0)
        return {"ok": ok, "output_tail": "" if ok else "FAILED tests/test_a.py::test_it - assert 1 == 2", "returncode": int(not ok)}

    def run(self, check=lambda: None) -> dict:
        return eng.run_rounds(request(), "BUILD", GRAPH, ask=self.ask, changed_paths=lambda: self.changed,
                              commit=self.commit, verify=self.verify, check=check)


def test_a_change_that_passes_the_engines_preflight_is_accepted_in_one_round() -> None:
    builder = Builder([["backend/a.py", "tests/test_a.py"]], [True])
    verdict = builder.run()
    assert verdict["ok"] and verdict["changed"] == ["backend/a.py", "tests/test_a.py"] and len(verdict["rounds"]) == 1
    assert builder.asked == [("BUILD", None)]


def test_a_preflight_failure_goes_back_to_the_same_session_and_the_repair_is_verified_again() -> None:
    builder = Builder([["backend/a.py"], ["backend/a.py", "tests/test_a.py"]], [False, True])
    verdict = builder.run()
    assert verdict["ok"] and [r["preflight_ok"] for r in verdict["rounds"]] == [False, True]
    # Codex P1 on #702: every candidate is committed BEFORE it is verified, and the verified head is the delivered one
    assert builder.events == ["commit", "verify", "commit", "verify"] and verdict["head"] == "head-2"
    repair, session = builder.asked[1]
    assert session == "session-1" and "assert 1 == 2" in repair          # the engine's own failure, to the same session


def test_out_of_scope_changes_are_never_verified_and_fail_the_build_if_they_persist() -> None:
    builder = Builder([["docs/x.md"]] * eng.MAX_ROUNDS, [])
    verdict = builder.run()
    assert not verdict["ok"] and verdict["reason"].startswith("CHANGES_OUTSIDE_SCOPE: OUTSIDE_ALLOWED_PATHS: docs/x.md")
    assert builder.verified == 0 and builder.events == [] and "OUTSIDE_ALLOWED_PATHS: docs/x.md" in builder.asked[1][0]


def test_rounds_are_bounded_and_a_build_that_never_passes_fails() -> None:
    builder = Builder([["backend/a.py"]] * 10, [False] * 10)
    verdict = builder.run()
    assert not verdict["ok"] and verdict["reason"] == "PREFLIGHT_FAILED_AFTER_REPAIR_ROUNDS"
    assert len(builder.asked) == eng.MAX_ROUNDS == 3


def test_a_builder_that_changes_nothing_reports_its_blocker_instead_of_a_pass() -> None:
    verdict = Builder([[]], []).run()
    assert not verdict["ok"] and verdict["reason"].startswith("BUILDER_CHANGED_NOTHING: I could not tell")


def test_a_failed_model_call_is_a_failure_not_a_pass() -> None:
    verdict = eng.run_rounds(request(), "BUILD", GRAPH, ask=lambda *_a: {"is_error": True, "result": "exit 1: not logged in"},
                             changed_paths=lambda: ["backend/a.py"], commit=lambda: "h", verify=lambda: {"ok": True},
                             check=lambda: None)
    assert not verdict["ok"] and verdict["reason"].startswith("BUILDER_CALL_FAILED: exit 1: not logged in")


def test_an_owner_takeover_stops_the_rounds() -> None:
    def revoked() -> None:
        raise RuntimeError(eng.INTERRUPTED_MARK)
    with pytest.raises(RuntimeError, match=eng.INTERRUPTED_MARK):
        Builder([["backend/a.py"]], [True]).run(check=revoked)


# ---- dispatch: the same ledger, budget and caps -------------------------------------------------------------------------

def test_an_in_engine_dispatch_writes_an_ordinary_ledger_row_and_starts_one_runner(tmp_path: Path) -> None:
    lane = Lane(tmp_path)
    result = lane.go()
    assert result["dispatched"] and result["transport"] == cb.ENGINE_TRANSPORT
    (row,) = lane.rows()
    assert row["status"] == "DISPATCHED" and row["routine"] == cb.ROUTINE and row["task_key"] == "RM-demo-1#a0"
    assert row["transport"] == "ENGINE" and row["pid"] == 4242 and row["lease_id"] == "lease-1"
    (job_file,) = lane.spawned
    job = json.loads(job_file.read_text(encoding="utf-8"))
    assert job["base"] == "a" * 40 and job["branch"] == f"iron-god/capable/{row['dispatch_id']}"
    assert "EDITING FILES" in job["prompt"] and job["request"]["id"] == "RM-demo-1"
    assert not list(cb.dispatch_state(tmp_path).glob("*.job.json"))   # the ledger directory holds dispatch rows only
    assert "RM-demo-1" in cb.open_request_ids(tmp_path)               # pending: the refill will not re-request it


def test_the_same_daily_budget_and_single_open_dispatch_bound_in_engine_builds(tmp_path: Path) -> None:
    lane = Lane(tmp_path)
    assert lane.go(daily_limit=1)["dispatched"]
    assert lane.go(request("RM-demo-2", "jweter/other"), daily_limit=1)["reason"] == "DAILY_BUDGET_EXHAUSTED"
    assert lane.go(request("RM-demo-3"))["reason"] == "REPOSITORY_HAS_OPEN_DISPATCH"
    assert len(lane.spawned) == 1


def test_a_busy_local_runtime_waits_without_spending_anything(tmp_path: Path) -> None:
    lane = Lane(tmp_path, busy=True)
    assert lane.go() == {"dispatched": False, "reason": "LOCAL_RUNTIME_BUSY"}
    assert lane.rows() == [] and lane.spawned == []


def test_without_the_cli_or_without_a_runner_nothing_is_dispatched_and_the_routine_is_never_a_fallback(tmp_path: Path) -> None:
    lane = Lane(tmp_path)
    missing = cb.dispatch_request(request(), lane_root=tmp_path, enabled=True, transport=cb.ENGINE_TRANSPORT,
                                  engine=lane.effects(), opener=forbidden_network, which=lambda _n: None, environ={})
    assert missing["reason"] == "CAPABLE_BUILDER_UNCONFIGURED" and "Claude Code CLI" in missing["needs"]
    assert lane.go(engine=None)["reason"] == "ENGINE_BUILDER_UNAVAILABLE"
    assert lane.go(enabled=False)["reason"] == "CAPABLE_BUILDER_DISABLED"
    assert lane.rows() == [] and lane.spawned == []


def test_a_runner_that_cannot_start_gives_the_lease_back_and_leaves_no_row(tmp_path: Path) -> None:
    lane = Lane(tmp_path)

    def broken(_job_file: Path) -> int:
        raise OSError("cannot start")
    with pytest.raises(OSError):
        lane.go(engine={**lane.effects(), "spawn": broken})
    assert lane.released == ["lease-1"] and lane.rows() == []


# ---- completion: the runner's own result, never silence -----------------------------------------------------------------

def test_a_delivered_build_is_recorded_as_a_product_pull_request_and_frees_the_runtime(tmp_path: Path) -> None:
    lane = Lane(tmp_path)
    lane.go()
    lane.finish(lane.rows()[0], {"ok": True, "pr_url": "https://github.com/jweter/VoiceStudio/pull/9", "rounds": [{}, {}]})
    (change,) = lane.reconcile()
    assert change["outcome"] == "PRODUCT_PR"
    (row,) = lane.rows()
    assert row["status"] == "PASS" and row["pr_url"].endswith("/pull/9") and "2 round" in row["notes"]
    assert lane.released == ["lease-1"] and "RM-demo-1" in cb.open_request_ids(tmp_path)  # its pull request is still open


def test_a_failed_build_spends_one_attempt_and_the_next_one_is_told_why(tmp_path: Path) -> None:
    lane = Lane(tmp_path)
    lane.go()
    lane.finish(lane.rows()[0], {"ok": False, "reason": "PREFLIGHT_FAILED_AFTER_REPAIR_ROUNDS"})
    assert lane.reconcile()[0]["outcome"] == "FAIL"
    assert cb.attempts_for(cb.dispatch_state(tmp_path), "RM-demo-1") == 1 and lane.released == ["lease-1"]

    assert lane.go()["task_key"] == "RM-demo-1#a1"
    assert "PREFLIGHT_FAILED_AFTER_REPAIR_ROUNDS" in json.loads(lane.spawned[1].read_text(encoding="utf-8"))["prompt"]
    lane.finish([r for r in lane.rows() if r["status"] == "DISPATCHED"][0], {"ok": False, "reason": "again"})
    lane.reconcile()
    assert lane.go()["reason"] == "ATTEMPTS_EXHAUSTED" and "RM-demo-1" in cb.spent_request_ids(tmp_path)


@pytest.mark.parametrize("result, alive", [
    ({"ok": False, "reason": "RuntimeError: EXECUTION_AUTHORITY_REVOKED"}, True),   # the runner saw the takeover
    (None, False),                                                                # the takeover killed the runner
])
def test_an_owner_takeover_keeps_the_record_and_spends_no_attempt(tmp_path: Path, result, alive) -> None:
    """Owner takeover is routine: it must never use up one of a slice's two capable attempts."""
    lane = Lane(tmp_path)
    lane.go()
    if result is not None:
        lane.finish(lane.rows()[0], result)
    assert lane.reconcile(alive=alive)[0]["outcome"] == "INTERRUPTED"
    (row,) = lane.rows()
    assert row["status"] == "INTERRUPTED" and lane.released == ["lease-1"]
    assert cb.attempts_for(cb.dispatch_state(tmp_path), "RM-demo-1") == 0 and "RM-demo-1" not in cb.open_request_ids(tmp_path)
    assert lane.go(daily_limit=1)["reason"] == "DAILY_BUDGET_EXHAUSTED"   # the model was used: the day's budget counts it
    assert lane.go()["task_key"] == "RM-demo-1#a1"                         # and the slice is simply built again


def test_interruptions_are_bounded(tmp_path: Path) -> None:
    lane = Lane(tmp_path)
    for _ in range(eng.MAX_INTERRUPTIONS):
        assert lane.go(daily_limit=99)["dispatched"]
        lane.reconcile(alive=False)
    assert lane.go(daily_limit=99)["reason"] == "INTERRUPTIONS_EXHAUSTED"
    assert "RM-demo-1" in cb.spent_request_ids(tmp_path)


def test_a_running_build_is_left_alone_and_an_overdue_one_is_stopped_before_it_is_failed(tmp_path: Path) -> None:
    lane = Lane(tmp_path)
    lane.go()
    assert lane.reconcile(alive=True) == [] and lane.rows()[0]["status"] == "DISPATCHED"
    assert lane.released == [] and lane.terminated == []
    late = time.time() + eng.JOB_TIMEOUT_S + 60
    assert lane.reconcile(alive=True, now=late)[0]["reason"] == "ENGINE_BUILD_TIMED_OUT"
    assert lane.terminated == [lane.rows()[0]["dispatch_id"]]
    assert lane.rows()[0]["status"] == "FAIL" and lane.released == ["lease-1"]


def test_an_overdue_runner_that_cannot_be_stopped_keeps_its_lease_and_its_slot(tmp_path: Path) -> None:
    """Codex P1 on #702: releasing the lease while the runner still works would put a second writer beside it."""
    lane = Lane(tmp_path)
    lane.go()
    late = time.time() + eng.JOB_TIMEOUT_S + 60
    (change,) = lane.reconcile(alive=True, now=late, stops=False)
    assert change["outcome"] == "OVERDUE_RUNNER_NOT_STOPPED"
    assert lane.rows()[0]["status"] == "DISPATCHED" and lane.released == []
    assert lane.go(request("RM-demo-2"))["reason"] == "REPOSITORY_HAS_OPEN_DISPATCH"   # no replacement is admitted

    lane.reconcile(alive=True, now=late + 60)                                          # the next pass stops it
    assert lane.rows()[0]["status"] == "FAIL" and lane.released == ["lease-1"] and len(lane.terminated) == 2


def test_the_overdue_bound_covers_every_round_the_runner_is_allowed(tmp_path: Path) -> None:
    assert eng.JOB_TIMEOUT_S >= eng.MAX_ROUNDS * (eng.ROUND_TIMEOUT_S + eng.PREFLIGHT_TIMEOUT_S)
    assert eng.JOB_TIMEOUT_S < cb.STALE_SECONDS   # the row is still "pending" for the refill while the runner may work


def test_only_this_dispatchs_own_runner_counts_as_alive_or_is_ever_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"dispatch_id": "abc123", "pid": 777}
    killed: list[int] = []
    lines = {777: f"python C:\\rel\\scripts\\{cb.RUNNER_SCRIPT} --job C:\\x\\abc123.job.json"}
    monkeypatch.setattr(cb, "process_command_line", lambda pid: lines.get(pid))
    monkeypatch.setattr(cb, "kill_process_tree", lambda pid: (killed.append(pid), lines.pop(pid, None)))

    assert cb.runner_alive(row) and not cb.runner_alive({**row, "pid": None})
    lines[888] = "chrome.exe --type=renderer"                       # the process id was reused by something else
    assert not cb.runner_alive({**row, "pid": 888}) and cb.terminate_runner({**row, "pid": 888}) and killed == []
    lines[999] = f"python {cb.RUNNER_SCRIPT} --job other-dispatch.job.json"   # another dispatch's runner
    assert not cb.runner_alive({**row, "pid": 999}) and cb.terminate_runner({**row, "pid": 999}) and killed == []

    assert cb.terminate_runner(row) and killed == [777]             # ours: stopped, and confirmed gone


def test_a_runner_that_survives_the_kill_or_cannot_be_looked_up_is_not_reported_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"dispatch_id": "abc123", "pid": 777}
    monkeypatch.setattr(cb, "process_command_line", lambda pid: f"python {cb.RUNNER_SCRIPT} --job abc123.job.json")
    monkeypatch.setattr(cb, "kill_process_tree", lambda pid: None)   # the kill had no effect
    assert cb.terminate_runner(row) is False

    def unanswerable(_pid: int) -> str:
        raise OSError("process lookup failed")
    monkeypatch.setattr(cb, "process_command_line", unanswerable)
    assert cb.runner_alive(row) is True                              # unknown is never "gone"


# ---- the runner: real git, a scripted model -----------------------------------------------------------------------------

def _runner():
    path = Path(__file__).resolve().parents[1] / "scripts" / "iron_god_capable_engine_runner.py"
    spec = importlib.util.spec_from_file_location("iron_god_capable_engine_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@localhost", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    repo = tmp_path / "clone"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "backend" / "old.py").write_text("y = 1\n", encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_changed_paths_sees_edits_new_files_and_both_sides_of_a_rename(clone: Path) -> None:
    runner = _runner()
    (clone / "backend" / "a.py").write_text("x = 2\n", encoding="utf-8")
    (clone / "tests").mkdir()
    (clone / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    _git(clone, "mv", "backend/old.py", "backend/new.py")
    base = _git(clone, "rev-parse", "HEAD")
    assert runner.changed_paths(clone, base) == ["backend/a.py", "backend/new.py", "backend/old.py", "tests/test_a.py"]

    head = runner.commit_candidate(clone, base, "candidate")          # committed changes still count as changed
    assert runner.changed_paths(clone, base) == ["backend/a.py", "backend/new.py", "backend/old.py", "tests/test_a.py"]
    (clone / "backend" / "a.py").write_text("x = 3\n", encoding="utf-8")
    again = runner.commit_candidate(clone, base, "candidate")         # a repair replaces the commit: still ONE on the base
    assert again != head and _git(clone, "rev-parse", "HEAD^") == base and _git(clone, "status", "--porcelain") == ""


def test_a_cli_answer_that_is_not_one_json_object_is_a_failed_call() -> None:
    runner = _runner()
    assert runner.parse_answer('{"session_id": "s", "is_error": false}\n', "", 0)["session_id"] == "s"
    assert runner.parse_answer("Not logged in", "", 1) == {"is_error": True, "result": "exit 1: Not logged in"}
    assert runner.parse_answer("", "boom", None)["is_error"] is True


def test_the_runner_builds_in_a_worktree_at_the_baseline_and_delivers_only_after_the_preflight(
        clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _runner()
    base = _git(clone, "rev-parse", "HEAD")
    worktree = tmp_path / "worktrees" / "capable-x"
    job = {"request": request(), "prompt": "BUILD", "clone": str(clone), "base": base, "worktree": str(worktree),
           "branch": "iron-god/capable/x", "model": None}
    events: list[str] = []

    class Runtime:
        def execute_supervised(self, req, _check, interval=1.0):
            assert Path(req.cwd) == worktree and req.argv[0] == "claude.exe" and "Bash" not in " ".join(req.argv)
            events.append("model")
            (worktree / "backend" / "a.py").write_text("x = 2\n", encoding="utf-8")

            class Done:
                stdout, stderr, returncode = json.dumps({"session_id": "s1", "is_error": False, "result": "done"}), "", 0
            return Done()

    def preflight(tree: Path) -> dict:
        events.append("preflight")
        # Codex P1 on #702: the candidate is already ONE commit on the baseline and the tree is clean, which is what
        # production_preflight's baseline comparison (HEAD^, clean tree) requires
        assert _git(tree, "rev-parse", "HEAD^") == base and _git(tree, "status", "--porcelain") == ""
        assert _git(tree, "show", "HEAD:backend/a.py") == "x = 2"
        (tree / "leftover.txt").write_text("written by a test run", encoding="utf-8")
        (tree / "backend" / "a.py").write_text("x = 99\n", encoding="utf-8")
        return {"ok": True, "command": "python engineering/preflight.py", "returncode": 0}

    def deliver(_job, tree, verdict, _check) -> str:
        events.append("deliver")
        assert verdict["changed"] == ["backend/a.py"] and tree == worktree
        # what is delivered is exactly the verified head: nothing the test run left behind
        assert verdict["head"] == _git(tree, "rev-parse", "HEAD") and _git(tree, "status", "--porcelain") == ""
        assert (tree / "backend" / "a.py").read_text(encoding="utf-8") == "x = 2\n" and not (tree / "leftover.txt").exists()
        return "https://github.com/jweter/VoiceStudio/pull/9"

    monkeypatch.setattr(runner, "NativeWindowsRuntime", Runtime)
    monkeypatch.setattr(runner, "deliver", deliver)
    verdict = runner.build(job, check=lambda: None, preflight=preflight, executable="claude.exe")

    assert verdict["ok"] and verdict["pr_url"].endswith("/pull/9") and events == ["model", "preflight", "deliver"]
    assert not worktree.exists()                                       # no worktree is left behind
    assert (clone / "backend" / "a.py").read_text(encoding="utf-8") == "x = 1\n"  # the lane's clone is untouched


def test_the_runner_never_delivers_a_build_whose_preflight_failed(clone: Path, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _runner()
    worktree = tmp_path / "worktrees" / "capable-y"
    job = {"request": request(), "prompt": "BUILD", "clone": str(clone), "base": _git(clone, "rev-parse", "HEAD"),
           "worktree": str(worktree), "branch": "iron-god/capable/y", "model": None}

    class Runtime:
        def execute_supervised(self, _req, _check, interval=1.0):
            (worktree / "backend" / "a.py").write_text("x = 3\n", encoding="utf-8")

            class Done:
                stdout, stderr, returncode = json.dumps({"session_id": "s1", "is_error": False}), "", 0
            return Done()

    def never(*_a):
        raise AssertionError("a failed build must not be committed, pushed or opened as a pull request")

    monkeypatch.setattr(runner, "NativeWindowsRuntime", Runtime)
    monkeypatch.setattr(runner, "deliver", never)
    verdict = runner.build(job, check=lambda: None, executable="claude.exe",
                           preflight=lambda _tree: {"ok": False, "output_tail": "1 failed"})
    assert not verdict["ok"] and verdict["reason"] == "PREFLIGHT_FAILED_AFTER_REPAIR_ROUNDS" and not worktree.exists()
    assert runner.build(job, check=lambda: None, preflight=never, executable=None)["reason"] == "CLAUDE_CLI_NOT_FOUND"


def test_delivery_refuses_a_tree_that_is_not_the_verified_head(clone: Path) -> None:
    runner = _runner()
    base = _git(clone, "rev-parse", "HEAD")
    (clone / "backend" / "a.py").write_text("x = 2\n", encoding="utf-8")
    head = runner.commit_candidate(clone, base, "candidate")
    job = {"request": request(), "branch": "iron-god/capable/z"}
    with pytest.raises(RuntimeError, match="DELIVERY_REFUSED"):
        runner.deliver(job, clone, {"head": "0" * 40, "changed": ["backend/a.py"]}, lambda: None)
    (clone / "backend" / "a.py").write_text("x = 5\n", encoding="utf-8")   # edited after verification
    with pytest.raises(RuntimeError, match="DELIVERY_REFUSED"):
        runner.deliver(job, clone, {"head": head, "changed": ["backend/a.py"]}, lambda: None)
