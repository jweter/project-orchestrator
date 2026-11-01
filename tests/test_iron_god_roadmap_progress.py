"""Durable roadmap progress: a mechanical, verified pull request that records merged slices in the capability graph."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from project_orchestrator import iron_god_calibration as calibration
from project_orchestrator import iron_god_roadmap_progress as progress
from project_orchestrator import iron_god_roadmap_refill as refill
from scripts.project_capability_graph import validate

REPO = "jweter/knowledge-engine-core"
SOURCE = "engineering/capability-graph.json"


def graph_payload() -> dict:
    def piece(sid: str, deps: list[str]) -> dict:
        row = {"id": sid, "state": "PLANNED", "capability_level": 1, "objective": f"Do {sid}.", "acceptance": [f"{sid} works"],
               "allowed_paths": ["pkg/**", "tests/**"]}
        if deps:
            row["depends_on"] = deps
        return row

    return {"schema_version": 1, "repository": REPO, "roadmap": ["cap-a"], "capabilities": [{
        "id": "cap-a", "capability": "Cap A", "state": "PLANNED", "depends_on": [], "completion_evidence": ["docs/x.md"],
        "slices": [piece("s1", []), piece("s2", ["s1"])]}]}


def run(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def world(tmp_path: Path):
    """A bare 'GitHub' origin, a clone whose main carries the declared graph, and the engine's runtime state."""
    origin, clone = tmp_path / "origin.git", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
    (clone / "pkg").mkdir()
    (clone / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8")
    (clone / "engineering").mkdir()
    (clone / SOURCE).write_text(json.dumps(graph_payload(), indent=2) + "\n", encoding="utf-8", newline="\n")
    plane = {"schema_version": 1, "capability_graph": SOURCE, "authoritative_documents": [SOURCE]}
    (clone / "engineering" / "control-plane.json").write_text(json.dumps(plane), encoding="utf-8")
    run(clone, "config", "user.email", "o@example.com")
    run(clone, "config", "user.name", "Owner")
    run(clone, "add", ".")
    run(clone, "commit", "-q", "-m", "seed")
    run(clone, "remote", "add", "origin", str(origin))
    run(clone, "push", "-q", "origin", "main")
    head = run(clone, "rev-parse", "HEAD")
    lane, state_root = tmp_path / "lane", tmp_path / "flow-state"
    lane.mkdir()
    state_root.mkdir()
    calibration.profile_path(state_root).write_text(json.dumps({
        "schema_version": 1, "ladder_version": calibration.LADDER_VERSION, "model": calibration.DEFAULT_MODEL,
        "hardware": calibration.hardware_fingerprint(), "max_passing_level": 3, "levels": {}}), encoding="utf-8")
    refill.refill_repository(lane, state_root, REPO, clone, head, items=[], state={}, reserved=set(), max_level=2)
    return {"tmp": tmp_path, "origin": origin, "clone": clone, "head": head, "lane": lane, "state_root": state_root}


def merged_item(world: dict) -> tuple[list[dict], dict, dict]:
    (request_path,) = (world["lane"] / refill.REQUESTS).glob("*.json")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    item = refill.stamp({"id": request["id"], "repository": REPO, "level": 1}, request, SOURCE)
    state = {"items": {item["id"]: {"attempts": 1, "outcome": "MERGED", "packets": ["PRODUCT-X-1"]}}}
    publications = {"PRODUCT-X-1": {"state": "MERGED", "merged_sha": "a" * 40, "pr": {"url": f"https://github.com/{REPO}/pull/7"}}}
    return [item], state, publications


# --- the pure edit ----------------------------------------------------------------------------------------------


def test_only_the_named_slices_state_and_verification_change() -> None:
    text = json.dumps(graph_payload(), indent=2) + "\n"

    edited = progress.apply_completions(text, [{"capability": "cap-a", "slice": "s1", "evidence": ["PR 7 merged as aaaa"]}])

    before, after = json.loads(text), json.loads(edited)
    assert after["capabilities"][0]["slices"][0]["state"] == "VERIFIED_COMPLETE"
    assert after["capabilities"][0]["slices"][0]["verification"] == ["PR 7 merged as aaaa"]
    after["capabilities"][0]["slices"][0].update(state="PLANNED")
    del after["capabilities"][0]["slices"][0]["verification"]
    assert after == before  # nothing else moved
    assert validate(json.loads(edited))["status"] != "DIRECTION_REQUIRED"


def test_the_edit_preserves_the_files_line_endings() -> None:
    text = (json.dumps(graph_payload(), indent=2) + "\n").replace("\n", "\r\n")

    edited = progress.apply_completions(text, [{"capability": "cap-a", "slice": "s1", "evidence": ["e"]}])

    assert "\r\n" in edited and "\n" not in edited.replace("\r\n", "")


@pytest.mark.parametrize("text,reason", [
    (json.dumps(graph_payload()) + "\n", "GRAPH_FORMAT_NOT_CANONICAL"),  # compact: an edit would reformat everything
    (json.dumps(graph_payload(), indent=4) + "\n", "GRAPH_FORMAT_NOT_CANONICAL"),
])
def test_a_graph_that_is_not_in_canonical_form_is_refused_not_reformatted(text: str, reason: str) -> None:
    with pytest.raises(progress.ProgressRefused, match=reason):
        progress.apply_completions(text, [{"capability": "cap-a", "slice": "s1", "evidence": ["e"]}])


def test_an_unknown_slice_is_refused() -> None:
    with pytest.raises(progress.ProgressRefused, match="SLICE_NOT_FOUND_IN_GRAPH"):
        progress.apply_completions(json.dumps(graph_payload(), indent=2) + "\n",
                                   [{"capability": "cap-a", "slice": "nope", "evidence": ["e"]}])


# --- evidence ---------------------------------------------------------------------------------------------------


def test_a_lane_merged_slice_is_reported_with_its_pull_request_and_merge_sha(world: dict) -> None:
    items, state, publications = merged_item(world)
    compiled = json.loads((world["state_root"] / "capability-graphs" / f"{REPO.replace('/', '__')}.json").read_text("utf-8"))

    (found,) = progress.completed_slices(REPO, compiled, items, state, publications, world["lane"])

    assert (found["capability"], found["slice"]) == ("cap-a", "s1")
    assert found["evidence"] == [f"https://github.com/{REPO}/pull/7 merged as {'a' * 12}"]


@pytest.mark.parametrize("outcome", ["FAIL: canonical preflight failed", "DISPATCHED", "ACCEPTED_LOCAL"])
def test_only_merged_work_is_recorded(world: dict, outcome: str) -> None:
    items, state, publications = merged_item(world)
    state["items"][items[0]["id"]]["outcome"] = outcome
    compiled = json.loads((world["state_root"] / "capability-graphs" / f"{REPO.replace('/', '__')}.json").read_text("utf-8"))

    assert progress.completed_slices(REPO, compiled, items, state, publications, world["lane"]) == []


# --- publishing: native preflight BEFORE a pull request exists, resumable at every step -------------------------


class Publisher:
    def __init__(self, fail_times: int = 0) -> None:
        self.calls: list[tuple] = []
        self.fail_times = fail_times

    def __call__(self, repository: str, branch: str, title: str, body: str) -> str:
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("PROGRESS_PR_FAILED: gh unavailable")
        self.calls.append((repository, branch, title, body))
        return f"https://github.com/{repository}/pull/99"


class Preflight:
    """Stands in for the separate preflight process; optionally finishes at once by writing the runner's result file."""

    def __init__(self, ok: bool = True, finish: bool = True, head: str | None = None, tail: str = "") -> None:
        self.ok, self.finish, self.head, self.tail = ok, finish, head, tail
        self.spawned: list[tuple[Path, Path]] = []

    def __call__(self, worktree: Path, result: Path) -> int:
        self.spawned.append((worktree, result))
        if self.finish:
            head = self.head or run(worktree, "rev-parse", "HEAD")
            result.parent.mkdir(parents=True, exist_ok=True)
            result.write_text(json.dumps({"ok": self.ok, "head": head, "output_tail": self.tail}), encoding="utf-8")
        return 4242


def step(world: dict, items, state, publications, *, publisher=None, preflight=None, lookup=None, may_start=True,
         now=None, reserve=None, release=None, pr_state=None) -> dict:
    return progress.record_progress(
        world["lane"], world["state_root"], REPO, world["clone"], world["head"], items=items, state=state,
        publications=publications, workspace=world["tmp"] / "scratch", publisher=publisher or Publisher(),
        spawn=preflight or Preflight(), pr_lookup=lookup or (lambda _r, _b: None), may_start=may_start,
        now=now or (lambda: 1000.0), reserve=reserve, release=release, pr_state=pr_state or (lambda _url: "OPEN"))


def remote_branches(world: dict) -> list[str]:
    return run(world["origin"], "for-each-ref", "--format=%(refname:short)", "refs/heads").split()


def test_nothing_is_pushed_or_published_until_the_repository_preflight_has_passed(world: dict) -> None:
    items, state, publications = merged_item(world)
    publisher, preflight = Publisher(), Preflight(finish=False)

    first = step(world, items, state, publications, publisher=publisher, preflight=preflight)
    still = step(world, items, state, publications, publisher=publisher, preflight=preflight, now=lambda: 1500.0)

    assert first["status"] == "VERIFYING" and still["status"] == "VERIFYING"
    assert len(preflight.spawned) == 1  # started once, not once per pass
    assert publisher.calls == [] and remote_branches(world) == ["main"]  # the review P1: no push before verification


def test_a_passing_preflight_for_the_exact_commit_then_publishes_one_pull_request_touching_only_the_graph(world: dict) -> None:
    items, state, publications = merged_item(world)
    publisher, preflight = Publisher(), Preflight()

    assert step(world, items, state, publications, publisher=publisher, preflight=preflight)["status"] == "VERIFYING"
    result = step(world, items, state, publications, publisher=publisher, preflight=preflight)

    assert result["status"] == "OPENED" and result["pr"].endswith("/pull/99") and result["slices"] == ["s1"]
    ((repository, branch, title, body),) = publisher.calls
    assert repository == REPO and branch.startswith(progress.BRANCH_PREFIX) and "record 1 merged slice" in title
    assert "pull/7 merged as" in body and "native preflight passed at this exact commit" in body
    pushed = run(world["origin"], "rev-parse", f"refs/heads/{branch}")
    assert run(world["origin"], "diff", "--name-only", world["head"], pushed).split() == [SOURCE]
    edited = json.loads(run(world["origin"], "show", f"{pushed}:{SOURCE}"))
    assert edited["capabilities"][0]["slices"][0]["state"] == "VERIFIED_COMPLETE"
    assert edited["capabilities"][0]["slices"][1]["state"] == "PLANNED"  # s2 is untouched
    assert edited["capabilities"][0]["state"] == "PLANNED"  # a capability is never completed here


@pytest.mark.parametrize("verdict", [
    {"ok": False, "tail": "ruff failed"},
    {"ok": True, "head": "f" * 40},  # a pass bound to a DIFFERENT commit is not a pass for this one
])
def test_a_failed_or_mismatched_preflight_publishes_nothing_and_blocks_with_a_reason(world: dict, verdict: dict) -> None:
    items, state, publications = merged_item(world)
    publisher = Publisher()
    preflight = Preflight(ok=verdict["ok"], head=verdict.get("head"), tail=verdict.get("tail", ""))
    step(world, items, state, publications, publisher=publisher, preflight=preflight)

    result = step(world, items, state, publications, publisher=publisher, preflight=preflight)
    again = step(world, items, state, publications, publisher=publisher, preflight=preflight)

    assert result["status"] == "BLOCKED" and result["reason"] == "PREFLIGHT_FAILED"
    assert again["status"] == "BLOCKED" and len(preflight.spawned) == 1  # sticky: no retry loop on the same base
    assert publisher.calls == [] and remote_branches(world) == ["main"]


def test_a_hung_preflight_is_failed_by_age_not_waited_on_forever(world: dict) -> None:
    items, state, publications = merged_item(world)
    preflight = Preflight(finish=False)
    step(world, items, state, publications, preflight=preflight, now=lambda: 0.0)

    result = step(world, items, state, publications, preflight=preflight,
                  now=lambda: progress.PREFLIGHT_TIMEOUT_S + 601.0)

    assert result["status"] == "BLOCKED" and result["reason"] == "PREFLIGHT_TIMED_OUT"


def test_heavy_verification_waits_for_an_idle_lane(world: dict) -> None:
    items, state, publications = merged_item(world)
    preflight = Preflight()

    waiting = step(world, items, state, publications, preflight=preflight, may_start=False)
    started = step(world, items, state, publications, preflight=preflight, may_start=True)

    assert waiting["status"] == "WAITING_FOR_IDLE" and started["status"] == "VERIFYING" and len(preflight.spawned) == 1


def test_a_failed_publish_after_the_push_resumes_instead_of_reporting_an_open_pull_request(world: dict) -> None:
    """Review P2: the push succeeded, PR creation failed, and every retry said ALREADY_OPEN without publishing."""
    items, state, publications = merged_item(world)
    publisher, preflight = Publisher(fail_times=1), Preflight()
    step(world, items, state, publications, publisher=publisher, preflight=preflight)
    with pytest.raises(RuntimeError, match="PROGRESS_PR_FAILED"):
        step(world, items, state, publications, publisher=publisher, preflight=preflight)
    assert len(remote_branches(world)) == 2 and publisher.calls == []  # the branch is on the remote, no PR exists

    resumed = step(world, items, state, publications, publisher=publisher, preflight=preflight)

    assert resumed["status"] == "OPENED" and len(publisher.calls) == 1
    waiting = step(world, items, state, publications, publisher=publisher, preflight=preflight)
    assert waiting["status"] == "WAITING_FOR_PROGRESS_PR"
    assert step(world, items, state, publications, publisher=publisher, preflight=preflight,
                pr_state=lambda _url: "CLOSED")["status"] == "ALREADY_OPEN"  # same set and base, closed: not retried blindly
    assert len(publisher.calls) == 1 and len(preflight.spawned) == 1  # nothing re-verified or re-published


def test_an_existing_pull_request_is_adopted_not_duplicated(world: dict) -> None:
    items, state, publications = merged_item(world)
    publisher = Publisher()
    existing = {"url": f"https://github.com/{REPO}/pull/55", "state": "OPEN"}
    step(world, items, state, publications, publisher=publisher)

    result = step(world, items, state, publications, publisher=publisher, lookup=lambda _r, _b: existing)

    assert result["status"] == "OPENED" and result["pr"].endswith("/pull/55") and publisher.calls == []


def test_an_unanswered_pull_request_lookup_never_creates_a_duplicate(world: dict) -> None:
    items, state, publications = merged_item(world)
    publisher = Publisher()
    step(world, items, state, publications, publisher=publisher)

    def unreachable(_repo: str, _branch: str):
        raise RuntimeError("PROGRESS_PR_LOOKUP_FAILED")

    with pytest.raises(RuntimeError, match="LOOKUP_FAILED"):
        step(world, items, state, publications, publisher=publisher, lookup=unreachable)
    assert publisher.calls == []


def test_a_verified_commit_that_changed_is_never_pushed(world: dict) -> None:
    items, state, publications = merged_item(world)
    preflight = Preflight()
    step(world, items, state, publications, preflight=preflight)
    tree = preflight.spawned[0][0]
    (tree / "pkg").mkdir(exist_ok=True)
    (tree / "pkg" / "sneaky.py").write_text("x = 2\n", encoding="utf-8")
    run(tree, "add", ".")
    run(tree, "-c", "user.name=x", "-c", "user.email=x@y", "commit", "-q", "-m", "tamper after verification")

    result = step(world, items, state, publications, preflight=preflight)

    assert result == {"status": "BLOCKED", "reason": "VERIFIED_COMMIT_CHANGED", "branch": result["branch"],
                      "detail": ""} and remote_branches(world) == ["main"]


def test_one_unfinished_job_per_repository_is_resumed_before_anything_newer(world: dict) -> None:
    items, state, publications = merged_item(world)
    preflight = Preflight(finish=False)
    first = step(world, items, state, publications, preflight=preflight)

    second = step(world, [], {}, {}, preflight=preflight)  # even with nothing newer to record, the open job is served

    assert first["status"] == "VERIFYING" and second["status"] == "VERIFYING" and len(preflight.spawned) == 1


def test_nothing_merged_means_nothing_is_started(world: dict) -> None:
    publisher, preflight = Publisher(), Preflight()

    result = step(world, [], {}, {}, publisher=publisher, preflight=preflight)

    assert result["status"] == "NOTHING_TO_RECORD" and publisher.calls == [] and preflight.spawned == []


def test_a_non_canonical_graph_is_a_blocker_and_starts_nothing(world: dict) -> None:
    items, state, publications = merged_item(world)
    (world["clone"] / SOURCE).write_text(json.dumps(graph_payload()) + "\n", encoding="utf-8", newline="\n")
    run(world["clone"], "commit", "-qam", "compact graph")
    run(world["clone"], "push", "-q", "origin", "main")
    world["head"] = run(world["clone"], "rev-parse", "HEAD")
    refill.refill_repository(world["lane"], world["state_root"], REPO, world["clone"], world["head"], items=[], state={},
                             reserved=set(), max_level=2)
    preflight = Preflight()

    result = step(world, items, state, publications, preflight=preflight)

    assert result == {"status": "BLOCKED", "reason": "GRAPH_FORMAT_NOT_CANONICAL"} and preflight.spawned == []


def test_a_graph_compiled_for_a_different_head_is_not_used(world: dict) -> None:
    items, state, publications = merged_item(world)
    world["head"] = "f" * 40

    assert step(world, items, state, publications)["status"] == "GRAPH_NOT_AT_HEAD"


def test_a_missing_compiled_graph_is_reported_not_guessed(world: dict) -> None:
    for path in (world["state_root"] / "capability-graphs").glob("*.json"):
        path.unlink()

    assert step(world, [], {}, {})["status"] == "NO_COMPILED_GRAPH"


def test_the_writer_never_merges_or_touches_owner_controls() -> None:
    source = Path(progress.__file__).read_text(encoding="utf-8")
    for forbidden in ("gh pr merge", "Enable-ScheduledTask", "owner_control", "branch -D", "--force", "reset --hard"):
        assert forbidden not in source


# --- the heavy preflight shares the runtime exclusion (found while rehearsing the whole loop) --------------------------


class LeasePool:
    def __init__(self, held: str | None = None) -> None:
        self.held, self.released = held, []

    def reserve(self) -> str | None:
        if self.held:
            return None
        self.held = "lease-p"
        return self.held

    def release(self, lease_id: str) -> None:
        self.released.append(lease_id)
        self.held = None


def test_the_preflight_holds_the_runtime_lease_while_it_runs_and_returns_it_when_the_result_is_in(world: dict) -> None:
    items, state, publications = merged_item(world)
    pool, preflight = LeasePool(), Preflight(finish=False)

    started = step(world, items, state, publications, preflight=preflight, reserve=pool.reserve, release=pool.release)

    assert started["status"] == "VERIFYING" and pool.held == "lease-p" and pool.reserve() is None and progress.verifying(
        world["tmp"] / "scratch")
    result_dir = world["tmp"] / "scratch" / "results"
    (job,) = progress._jobs(world["tmp"] / "scratch")
    Path(job["result"]).parent.mkdir(parents=True, exist_ok=True)
    Path(job["result"]).write_text(json.dumps({"ok": True, "head": job["commit_sha"]}), encoding="utf-8")
    finished = step(world, items, state, publications, preflight=preflight, reserve=pool.reserve, release=pool.release)

    assert finished["status"] == "OPENED" and pool.held is None and pool.released == ["lease-p"] and result_dir.exists()
    assert not progress.verifying(world["tmp"] / "scratch")


def test_the_preflight_waits_while_the_local_model_is_in_use(world: dict) -> None:
    items, state, publications = merged_item(world)
    pool, preflight = LeasePool(held="a-worker"), Preflight()

    waiting = step(world, items, state, publications, preflight=preflight, reserve=pool.reserve, release=pool.release)

    assert waiting["status"] == "WAITING_FOR_IDLE" and preflight.spawned == []
    pool.held = None
    assert step(world, items, state, publications, preflight=preflight, reserve=pool.reserve,
                release=pool.release)["status"] == "VERIFYING"


def test_a_failed_a_hung_or_an_unlaunchable_preflight_still_returns_the_lease(world: dict) -> None:
    items, state, publications = merged_item(world)
    pool = LeasePool()
    step(world, items, state, publications, preflight=Preflight(ok=False), reserve=pool.reserve, release=pool.release)
    failed = step(world, items, state, publications, reserve=pool.reserve, release=pool.release)
    assert failed["reason"] == "PREFLIGHT_FAILED" and pool.held is None and pool.released == ["lease-p"]


def test_a_hung_preflight_gives_its_lease_back(world: dict) -> None:
    items, state, publications = merged_item(world)
    pool = LeasePool()
    step(world, items, state, publications, preflight=Preflight(finish=False), reserve=pool.reserve,
         release=pool.release, now=lambda: 0.0)

    result = step(world, items, state, publications, reserve=pool.reserve, release=pool.release,
                  now=lambda: progress.PREFLIGHT_TIMEOUT_S + 601.0)

    assert result["reason"] == "PREFLIGHT_TIMED_OUT" and pool.held is None


def test_a_launch_that_raises_returns_the_lease(world: dict) -> None:
    items, state, publications = merged_item(world)
    pool = LeasePool()

    def broken(*_a):
        raise OSError("cannot launch")

    with pytest.raises(OSError):
        step(world, items, state, publications, preflight=broken, reserve=pool.reserve, release=pool.release)
    assert pool.held is None and pool.released == ["lease-p"]


# --- one open progress pull request per repository (found while rehearsing the whole loop) -------------------------------


def second_slice_merged(world: dict, items: list, state: dict, publications: dict) -> tuple[list, dict, dict]:
    request = {"exact_head": world["head"], "capability": "cap-a", "slice": "s2", "route": {"builder": "LOCAL"},
               "capability_level": 1}
    item = refill.stamp({"id": refill.item_id(REPO, "cap-a", "s2"), "repository": REPO, "level": 1}, request, SOURCE)
    state = {"items": {**state["items"], item["id"]: {"attempts": 1, "outcome": "MERGED", "packets": ["PRODUCT-X-2"]}}}
    publications = {**publications, "PRODUCT-X-2": {"state": "MERGED", "merged_sha": "b" * 40,
                                                    "pr": {"url": f"https://github.com/{REPO}/pull/8"}}}
    return [*items, item], state, publications


def test_a_second_progress_pr_waits_while_the_first_is_open_and_proceeds_once_it_merged(world: dict) -> None:
    items, state, publications = merged_item(world)
    publisher = Publisher()
    step(world, items, state, publications, publisher=publisher)
    assert step(world, items, state, publications, publisher=publisher)["status"] == "OPENED"
    items, state, publications = second_slice_merged(world, items, state, publications)

    waiting = step(world, items, state, publications, publisher=publisher, pr_state=lambda _url: "OPEN")
    unknown = step(world, items, state, publications, publisher=publisher, pr_state=lambda _url: None)

    assert waiting["status"] == "WAITING_FOR_PROGRESS_PR" and unknown["status"] == "WAITING_FOR_PROGRESS_PR"
    assert len(publisher.calls) == 1  # no second, conflicting pull request on the same file
    # the first progress PR merges: main now shows s1 complete, so the next one records only s2
    run(world["origin"], "update-ref", "refs/heads/main", run(world["origin"], "rev-parse", f"refs/heads/{publisher.calls[0][1]}"))
    run(world["clone"], "fetch", "-q", "origin", "main")
    run(world["clone"], "merge", "-q", "--ff-only", "origin/main")
    world["head"] = run(world["clone"], "rev-parse", "HEAD")
    refill.refill_repository(world["lane"], world["state_root"], REPO, world["clone"], world["head"], items=[], state={},
                             reserved=set(), max_level=2)
    step(world, items, state, publications, publisher=publisher, pr_state=lambda _url: "MERGED")
    done = step(world, items, state, publications, publisher=publisher, pr_state=lambda _url: "MERGED")

    assert done["status"] == "OPENED" and done["slices"] == ["s2"] and len(publisher.calls) == 2


# --- review P2 on #609: verification is settled independently of repository sync ----------------------------------------


def test_a_finished_preflight_is_settled_and_its_lease_returned_with_no_repository_access(world: dict) -> None:
    items, state, publications = merged_item(world)
    pool = LeasePool()
    step(world, items, state, publications, preflight=Preflight(finish=False), reserve=pool.reserve, release=pool.release)
    (job,) = progress._jobs(world["tmp"] / "scratch")
    Path(job["result"]).parent.mkdir(parents=True, exist_ok=True)
    Path(job["result"]).write_text(json.dumps({"ok": True, "head": job["commit_sha"]}), encoding="utf-8")
    assert progress.verifying(world["tmp"] / "scratch") and pool.held == "lease-p"

    failures = progress.harvest(world["tmp"] / "scratch", release=pool.release)  # no clone, no graph, no GitHub

    assert failures == [] and not progress.verifying(world["tmp"] / "scratch")
    assert pool.held is None and progress._jobs(world["tmp"] / "scratch")[0]["stage"] == "VERIFIED"


def test_a_failed_or_hung_preflight_is_settled_globally_and_reported_with_its_repository(world: dict) -> None:
    items, state, publications = merged_item(world)
    pool = LeasePool()
    step(world, items, state, publications, preflight=Preflight(finish=False), reserve=pool.reserve,
         release=pool.release, now=lambda: 0.0)

    assert progress.harvest(world["tmp"] / "scratch", now=lambda: 60.0, release=pool.release) == []  # still within its time
    failures = progress.harvest(world["tmp"] / "scratch", now=lambda: progress.PREFLIGHT_TIMEOUT_S + 601.0,
                                release=pool.release)

    assert [(f["repository"], f["reason"]) for f in failures] == [(REPO, "PREFLIGHT_TIMED_OUT")]
    assert pool.held is None and not progress.verifying(world["tmp"] / "scratch")
    assert progress.harvest(world["tmp"] / "scratch") == []  # settled once


def test_a_verified_job_published_later_still_pushes_only_the_verified_commit(world: dict) -> None:
    items, state, publications = merged_item(world)
    publisher, preflight = Publisher(), Preflight()
    step(world, items, state, publications, publisher=publisher, preflight=preflight)
    assert progress.harvest(world["tmp"] / "scratch") == []  # verification settled by the global pass

    result = step(world, items, state, publications, publisher=publisher, preflight=preflight)

    assert result["status"] == "OPENED" and len(publisher.calls) == 1 and len(preflight.spawned) == 1
