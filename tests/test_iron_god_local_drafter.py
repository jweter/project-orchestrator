"""The local builder drafts Level 1 roadmap slices; the strict admission still decides (issue #594)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_calibration as calibration
from project_orchestrator import iron_god_local_drafter as drafter
from project_orchestrator import iron_god_roadmap_refill as refill
from scripts import iron_god_draft_author as author

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("worker_for_drafter_tests", ROOT / "scripts" / "iron_god_product_worker.py")
WORKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER)

REPO = "jweter/VoiceStudio"
SOURCE = "engineering/capability-graph.json"
GOOD_PLAN = {
    "function_name": "newest_entries", "signature": "def newest_entries(items: list, limit: int) -> list:",
    "module_path": "pkg/newest_entries.py",
    "objective": "Return the last `limit` items of the list in their original order; return an empty list when limit is not positive.",
    "examples": [{"call": "newest_entries([1, 2, 3, 4], 2)", "why": "the last two items are 3 and 4", "expected": [3, 4]},
                 {"call": "newest_entries([1, 2], 5)", "why": "fewer items than the limit returns all of them", "expected": [1, 2]},
                 {"call": "newest_entries([1, 2, 3], 0)", "why": "a non-positive limit returns nothing", "expected": []}],
    "note": "Added pkg.newest_entries.newest_entries; next: wire it to the cache pruner.",
}


ACCEPTANCE = [
    {"call": "{fn}([1, 2, 3, 4], 2)", "expected": [3, 4], "why": "the newest two entries are the last two, in order"},
    {"call": "{fn}([1, 2, 3], 0)", "expected": [], "why": "a non-positive count keeps nothing"},
]


def graph(level: int | None = 1) -> dict:
    row = {"id": "s1", "state": "PLANNED", "objective": "Keep only the newest entries of a list.",
           "acceptance": ["returns the last N items in order", "a non-positive N returns an empty list"],
           "allowed_paths": ["pkg/**", "tests/**"], "forbidden_paths": ["pkg/secret/**"],
           "acceptance_examples": ACCEPTANCE}
    if level is not None:
        row["capability_level"] = level
    return {"schema_version": 1, "repository": REPO, "roadmap": ["cap-a"], "capabilities": [{
        "id": "cap-a", "capability": "Cap A", "state": "PLANNED", "depends_on": [], "completion_evidence": ["docs/x.md"],
        "slices": [row]}]}


def run_git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def world(tmp_path: Path):
    clone = tmp_path / "clone"
    (clone / "pkg").mkdir(parents=True)
    (clone / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (clone / "pkg" / "core.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (clone / "pkg" / "secret").mkdir()  # a real package the slice nevertheless forbids
    (clone / "pkg" / "secret" / "__init__.py").write_text("", encoding="utf-8")
    (clone / "engineering").mkdir()
    (clone / SOURCE).write_text(json.dumps(graph(), indent=2) + "\n", encoding="utf-8", newline="\n")
    plane = {"schema_version": 1, "capability_graph": SOURCE, "authoritative_documents": [SOURCE]}
    (clone / "engineering" / "control-plane.json").write_text(json.dumps(plane), encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
    run_git(clone, "config", "user.email", "o@example.com")
    run_git(clone, "config", "user.name", "Owner")
    run_git(clone, "add", ".")
    run_git(clone, "commit", "-q", "-m", "seed")
    head = run_git(clone, "rev-parse", "HEAD")
    lane, state_root = tmp_path / "lane", tmp_path / "flow-state"
    lane.mkdir()
    state_root.mkdir()
    calibration.profile_path(state_root).write_text(json.dumps({
        "schema_version": 1, "ladder_version": calibration.LADDER_VERSION, "model": calibration.DEFAULT_MODEL,
        "hardware": calibration.hardware_fingerprint(), "max_passing_level": 3, "levels": {}}), encoding="utf-8")
    return {"clone": clone, "head": head, "lane": lane, "state_root": state_root, "tmp": tmp_path}


def refill_pass(world: dict, **kw) -> dict:
    return refill.refill_repository(world["lane"], world["state_root"], REPO, world["clone"], world["head"], items=[],
                                    state={}, reserved=set(), max_level=2, **kw)


def request_of(world: dict, **over) -> dict:
    (request,) = refill_pass(world)["local_requests"]
    request.update(over)
    return request


class Spawner:
    """Records launches; with `finish` it plays the runner in-process using a fake planner and the REAL validator."""

    def __init__(self, world: dict | None = None, plan: object = GOOD_PLAN, finish: bool = False) -> None:
        self.world, self.plan, self.finish = world, plan, finish
        self.launched: list[tuple[Path, Path, str, Path]] = []

    def __call__(self, request_file: Path, clone: Path, base: str, result: Path) -> int:
        self.launched.append((request_file, clone, base, result))
        if self.finish:
            request = json.loads(request_file.read_text(encoding="utf-8"))
            author.run(request, clone, base, result, planner=lambda *_a: self.plan, validator=WORKER.validate_plan,
                       authority=lambda: True, roots=["pkg/"], forbidden=[".github/", "engineering/", "docs/"],
                       attempted=request.get("attempted_functions", []))
        return 31337


# --- the plan becomes an ordinary backlog draft ------------------------------------------------------------------


def validated_good(world: dict, request: dict) -> dict:
    packet = author.build_packet(request, ["pkg/"], world["head"], [".github/", "engineering/", "docs/"], [])
    return WORKER.validate_plan(GOOD_PLAN, world["clone"], world["head"], packet)


def test_a_validated_plan_becomes_a_draft_the_unchanged_admission_accepts(world: dict) -> None:
    request = request_of(world)

    draft = drafter.plan_to_draft(request, GOOD_PLAN, validated_good(world, request))

    assert backlog.item_problems({**draft, "status": "approved", "assignee": backlog.AGENT_ID}) == []
    assert draft["id"] == request["id"] and draft["roadmap_ref"] == request["roadmap_ref"] and draft["level"] == 1
    assert draft["build"]["module_path"] == "pkg/newest_entries.py" and len(draft["build"]["examples"]) == 3
    assert refill.draft_problems(draft, request, items=[], reserved=set(), head=world["head"],
                                 changed_since=lambda *_a: set(), max_level=2) == []


def test_examples_the_validator_dropped_are_not_carried_into_the_draft(world: dict) -> None:
    request = request_of(world)
    validated = validated_good(world, request)
    plan = {**GOOD_PLAN, "examples": [*GOOD_PLAN["examples"], {"call": "newest_entries(NOT_LITERAL, 1)", "why": "x", "expected": []}]}

    draft = drafter.plan_to_draft(request, plan, validated)

    # the roadmap's two examples first, then the plan's one remaining own example; the unusable one is gone
    assert [e["call"] for e in draft["build"]["examples"]] == [
        "newest_entries([1, 2, 3, 4], 2)", "newest_entries([1, 2, 3], 0)", "newest_entries([1, 2], 5)"]


# --- who may be drafted -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("route,level,expected", [
    ({"builder": "LOCAL"}, 1, True), ({"builder": "LOCAL"}, 2, False),  # level 2 needs approved failing tests
    ({"builder": "CAPABLE"}, 1, False), ({"builder": "NONE"}, 1, False), (None, 1, False)])
def test_only_local_routed_level_one_requests_with_roadmap_examples_are_drafted(route, level, expected) -> None:
    assert drafter.eligible({"route": route, "capability_level": level, "acceptance_examples": ACCEPTANCE}) is expected


@pytest.mark.parametrize("examples", [
    [], ACCEPTANCE[:1],  # fewer than the two the builder must be anchored by
    [{"call": "{fn}(1)", "expected": 1}, {"call": "{fn}(2)", "expected": 2}],  # no stated reason
    [{"call": "other(1)", "expected": 1, "why": "x"}, {"call": "{fn}(1)", "expected": 1, "why": "x"}],  # no {fn}
    [{"call": "{fn}(os.getcwd())", "expected": 1, "why": "x"}, {"call": "{fn}(1)", "expected": 1, "why": "x"}]])  # not literal
def test_a_slice_without_usable_roadmap_examples_is_not_drafted_locally(examples) -> None:
    request = {"route": {"builder": "LOCAL"}, "capability_level": 1, "acceptance_examples": examples}

    assert drafter.eligible(request) is False and drafter.missing_examples(request) is True


# --- jobs ---------------------------------------------------------------------------------------------------------


def test_one_job_starts_with_the_roadmap_request_and_nothing_else_runs_beside_it(world: dict) -> None:
    request = request_of(world)
    spawner = Spawner()

    first = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    second = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    assert first == [{"id": request["id"], "status": "STARTED", "attempt": 0, "repository": REPO}] and second == []
    assert len(spawner.launched) == 1 and drafter.drafting_in_progress(world["lane"])
    copy = json.loads(spawner.launched[0][0].read_text(encoding="utf-8"))
    assert copy["objective"] == "Keep only the newest entries of a list." and copy["roadmap_ref"] == request["roadmap_ref"]


def test_nothing_starts_while_a_worker_is_active(world: dict) -> None:
    spawner = Spawner()

    outcomes = drafter.advance(world["lane"], [request_of(world)], world["clone"], world["head"], spawn=spawner,
                               may_start=False)

    assert outcomes == [] and spawner.launched == [] and not drafter.drafting_in_progress(world["lane"])


def test_only_one_job_runs_at_a_time_across_requests(world: dict) -> None:
    one = request_of(world)
    two = {**one, "id": one["id"] + "-other"}
    spawner = Spawner()

    outcomes = drafter.advance(world["lane"], [one, two], world["clone"], world["head"], spawn=spawner)

    assert [o["status"] for o in outcomes] == ["STARTED"] and len(spawner.launched) == 1


def test_a_finished_job_writes_the_draft_for_admission(world: dict) -> None:
    request = request_of(world)
    spawner = Spawner(finish=True)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    outcomes = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    assert outcomes == [{"id": request["id"], "status": "DRAFTED", "repository": REPO}]
    written = json.loads((world["lane"] / refill.DRAFTS / f"{request['id']}.json").read_text(encoding="utf-8"))
    assert written["build"]["function_name"] == "newest_entries" and not drafter.drafting_in_progress(world["lane"])


def test_a_rejected_plan_is_a_recorded_failure_and_writes_no_draft(world: dict) -> None:
    request = request_of(world)
    bad = {**GOOD_PLAN, "module_path": "other/outside.py"}  # outside the allowed package envelope
    spawner = Spawner(plan=bad, finish=True)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    outcomes = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, may_start=False)

    assert outcomes[0]["status"] == "FAILED" and "PLAN_PATH_OUTSIDE_ENVELOPE" in outcomes[0]["reason"]
    assert not (world["lane"] / refill.DRAFTS).exists()


def test_two_failed_attempts_end_local_drafting_for_that_request(world: dict) -> None:
    request = request_of(world)
    spawner = Spawner(plan={"function_name": "x"}, finish=True)
    for _ in range(drafter.MAX_ATTEMPTS):
        drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)  # start
        drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, may_start=False)  # harvest

    third = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    assert third == [] and len(spawner.launched) == drafter.MAX_ATTEMPTS


def test_a_hung_job_is_failed_by_age(world: dict) -> None:
    request = request_of(world)
    spawner = Spawner()
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, now=lambda: 0.0)

    waiting = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, now=lambda: 60.0)
    timed_out = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner,
                                now=lambda: drafter.DRAFT_TIMEOUT_S + 1.0, may_start=False)

    assert waiting == [] and timed_out[0]["reason"] == "DRAFT_TIMED_OUT" and not drafter.drafting_in_progress(world["lane"])


def test_a_finished_job_is_harvested_even_while_another_repository_is_refilled(world: dict) -> None:
    """Review P1: a VoiceStudio result seen during another repository's pass was discarded as 'no longer current'."""
    request = request_of(world)
    spawner = Spawner(finish=True)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    other_repository_pass = drafter.advance(world["lane"], [], world["clone"], world["head"], spawn=spawner)

    assert other_repository_pass == [{"id": request["id"], "status": "DRAFTED", "repository": REPO}]
    assert (world["lane"] / refill.DRAFTS / f"{request['id']}.json").is_file()
    assert drafter.harvest(world["lane"]) == []  # harvested once


def test_the_standalone_harvest_finishes_jobs_with_no_refill_pass_at_all(world: dict) -> None:
    request = request_of(world)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=Spawner(finish=True))

    assert drafter.harvest(world["lane"])[0]["status"] == "DRAFTED" and not drafter.drafting_in_progress(world["lane"])


def test_an_unjudged_draft_is_waited_for_and_a_rejected_one_is_retried_with_the_reasons(world: dict) -> None:
    request = request_of(world)
    drafts = world["lane"] / refill.DRAFTS
    drafts.mkdir(parents=True)
    (drafts / f"{request['id']}.json").write_text("{}", encoding="utf-8")
    spawner = Spawner()

    assert drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner) == []  # unjudged

    import hashlib
    digest = hashlib.sha256((drafts / f"{request['id']}.json").read_bytes()).hexdigest()
    refill._mutate(world["lane"], lambda store: (store["processed"].update({request["id"]: digest}),
                                                refill._log(store, "REJECTED", REPO, request["id"],
                                                            {"problems": ["PATH_OUTSIDE_ROADMAP_SCOPE"]})))
    retried = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    assert retried[0]["status"] == "STARTED"
    copy = json.loads(spawner.launched[0][0].read_text(encoding="utf-8"))
    assert copy["planner_feedback"] == "PATH_OUTSIDE_ROADMAP_SCOPE"


def test_a_ready_result_is_noticed_without_waiting_for_the_interval(world: dict) -> None:
    request = request_of(world)
    spawner = Spawner(finish=True)
    assert drafter.has_result_ready(world["lane"]) is False
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    assert drafter.has_result_ready(world["lane"]) is True


# --- end to end: request -> local draft -> unchanged admission -> a dispatchable approved item ------------------------


def test_the_local_draft_is_admitted_and_becomes_a_dispatchable_level_one_item(world: dict) -> None:
    request = request_of(world)
    spawner = Spawner(finish=True)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, may_start=False)

    report = refill_pass(world)

    assert report["admitted"] == [request["id"]]
    (item,) = refill.admitted_items(world["lane"])
    assert backlog.item_problems(item) == [] and item["origin"] == "roadmap-derived" and item["level"] == 1
    assert backlog.next_item([item], {}, REPO, max_level=2) == item  # the existing dispatcher accepts it
    assert item["authorization"]["declared_capability_level"] == 1 and item["authorization"]["route"] == refill.LOCAL


def test_a_plan_that_escapes_the_roadmap_scope_is_rejected_by_admission_even_if_the_worker_accepts_it(world: dict) -> None:
    request = request_of(world)
    forbidden = {**GOOD_PLAN, "module_path": "pkg/secret/newest_entries.py"}  # inside the package, but forbidden by the slice
    spawner = Spawner(plan=forbidden, finish=True)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, may_start=False)

    report = refill_pass(world)

    assert report["admitted"] == [] and refill.admitted_items(world["lane"]) == [] and report["rejected"] == [request["id"]]
    problems = refill.load_store(world["lane"])["ledger"][-1]["detail"]["problems"]
    assert "PATH_OUTSIDE_ROADMAP_SCOPE" in problems  # the slice's own forbidden path, enforced by admission, not the worker


# --- review P1: a model cannot certify its own work - the roadmap's examples anchor the draft ------------------------


def test_the_roadmap_acceptance_examples_are_injected_for_the_chosen_function_name(world: dict) -> None:
    request = request_of(world)

    draft = drafter.plan_to_draft(request, GOOD_PLAN, validated_good(world, request))

    calls = {e["call"]: e for e in draft["build"]["examples"]}
    assert calls["newest_entries([1, 2, 3, 4], 2)"]["why"].startswith("roadmap acceptance example")
    assert calls["newest_entries([1, 2, 3], 0)"]["expected"] == []
    assert any("(roadmap acceptance)" in c for c in draft["acceptance_criteria"])
    assert "Roadmap slice goal: Keep only the newest entries of a list." in draft["build"]["objective"]
    assert len(draft["build"]["objective"]) <= 600 and draft["build"]["objective"] == draft["objective"]


def test_a_plan_that_contradicts_the_roadmap_is_replaced_by_the_roadmap_not_trusted(world: dict) -> None:
    """Review P1 reproduction (the planner returns the FIRST N items) and S2-r1: the roadmap alone defines the draft."""
    request = request_of(world)
    first_n = {**GOOD_PLAN, "objective": "Return the first `limit` items of the list in their original order, empty if limit < 1.",
               "examples": [{"call": "newest_entries([1, 2, 3, 4], 2)", "why": "the first two are 1 and 2", "expected": [1, 2]},
                            {"call": "newest_entries([5, 6], 9)", "why": "fewer items than the limit returns all", "expected": [5, 6]},
                            {"call": "newest_entries([7], 0)", "why": "a non-positive limit returns nothing", "expected": []}]}
    spawner = Spawner(plan=first_n, finish=True)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    outcomes = drafter.harvest(world["lane"])

    assert outcomes[0]["status"] == "DRAFTED"
    draft = json.loads((world["lane"] / refill.DRAFTS / f"{request['id']}.json").read_text(encoding="utf-8"))
    build = draft["build"]
    assert "first `limit`" not in build["objective"] and build["objective"].startswith("Roadmap slice goal:")
    assert all(e["why"].startswith("roadmap acceptance example") for e in build["examples"])  # none of the model's own
    assert "discarded" in build["note"]
    assert backlog.item_problems({**draft, "status": "approved", "assignee": backlog.AGENT_ID}) == []


def test_discarded_planner_interpretation_cannot_leave_contradictory_acceptance_criteria(world: dict) -> None:
    request = request_of(world)
    plan = {**GOOD_PLAN, "examples": [
        {"call": "newest_entries([1, 2, 3, 4], 2)", "why": "first two", "expected": [1, 2]},
        *GOOD_PLAN["examples"][1:],
    ]}
    validated = {**validated_good(world, request), "acceptance_criteria": [
        "newest_entries([1, 2, 3, 4], 2) returns [1, 2] (planner assertion)",
    ]}
    draft = drafter.plan_to_draft(request, plan, validated)
    criteria = draft["acceptance_criteria"]
    assert "returns the last N items in order" in criteria
    assert any("returns [3, 4] (roadmap acceptance)" in criterion for criterion in criteria)
    assert not any("returns [1, 2]" in criterion for criterion in criteria)
    assert all("planner assertion" not in criterion for criterion in criteria)


def test_a_plan_whose_own_examples_avoid_the_roadmap_calls_still_carries_the_roadmap_tests(world: dict) -> None:
    """Even an evasive plan cannot drop the owner's examples: they travel with the build spec into the builder's tests."""
    request = request_of(world)
    evasive = {**GOOD_PLAN, "examples": [
        {"call": "newest_entries([9, 8, 7], 1)", "why": "the newest one entry is the last", "expected": [7]},
        {"call": "newest_entries([], 3)", "why": "nothing to keep", "expected": []},
        {"call": "newest_entries([1], 5)", "why": "fewer than asked returns all", "expected": [1]}]}
    packet = author.build_packet(request, ["pkg/"], world["head"], [".github/"], [])
    validated = WORKER.validate_plan(evasive, world["clone"], world["head"], packet)

    draft = drafter.plan_to_draft(request, evasive, validated)

    expected_calls = {"newest_entries([1, 2, 3, 4], 2)", "newest_entries([1, 2, 3], 0)"}
    assert expected_calls <= {e["call"] for e in draft["build"]["examples"]}
    assert backlog.item_problems({**draft, "status": "approved", "assignee": backlog.AGENT_ID}) == []


def test_a_slice_without_roadmap_examples_asks_for_them_and_starts_nothing(world: dict) -> None:
    request = request_of(world)
    request["acceptance_examples"] = []
    spawner = Spawner()

    outcomes = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    assert outcomes[0]["status"] == "NEEDS_ACCEPTANCE_EXAMPLES" and "acceptance_examples" in outcomes[0]["reason"]
    assert spawner.launched == []


def test_the_guide_shows_the_planner_the_owners_examples() -> None:
    text = author.guide_text({**REQUEST_FOR_GUIDE, "acceptance_examples": ACCEPTANCE})

    assert "{fn}([1, 2, 3, 4], 2) -> [3, 4]" in text and "MUST satisfy every one" in text


REQUEST_FOR_GUIDE = {"id": "RM-x", "roadmap_ref": "g#c/s", "objective": "Keep the newest entries.", "slice_acceptance": [],
                     "allowed_paths": ["pkg/**"], "forbidden_paths": []}


# --- review P2: the draft shares the runtime exclusion --------------------------------------------------------------


class Pool:
    """A one-slot lease pool standing in for RuntimeLeasePool."""

    def __init__(self) -> None:
        self.held: str | None = None
        self.released: list[str] = []

    def reserve(self) -> str | None:
        if self.held:
            return None
        self.held = "lease-1"
        return self.held

    def release(self, lease_id: str) -> None:
        self.released.append(lease_id)
        self.held = None


def test_a_draft_job_holds_the_runtime_lease_and_gives_it_back_when_it_ends(world: dict) -> None:
    request = request_of(world)
    pool, spawner = Pool(), Spawner(finish=True)

    started = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner,
                              reserve=pool.reserve, release=pool.release)

    assert started[0]["status"] == "STARTED" and pool.held == "lease-1" and pool.reserve() is None  # nobody else gets it
    drafter.harvest(world["lane"], release=pool.release)
    assert pool.held is None and pool.released == ["lease-1"]


def test_no_draft_starts_while_the_model_is_in_use_and_a_failed_launch_returns_the_lease(world: dict) -> None:
    request = request_of(world)
    pool = Pool()
    pool.held = "a-worker"
    spawner = Spawner()

    assert drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, reserve=pool.reserve,
                           release=pool.release) == [] and spawner.launched == []
    pool.held = None

    def broken(*_a):
        raise OSError("cannot launch")

    with pytest.raises(OSError):
        drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=broken, reserve=pool.reserve,
                        release=pool.release)
    assert pool.held is None and pool.released == ["lease-1"]  # nothing leaks


def test_a_hung_job_gives_its_lease_back(world: dict) -> None:
    request = request_of(world)
    pool = Pool()
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=Spawner(), reserve=pool.reserve,
                    release=pool.release, now=lambda: 0.0)

    drafter.harvest(world["lane"], now=lambda: drafter.DRAFT_TIMEOUT_S + 1.0, release=pool.release)

    assert pool.held is None


# --- Level 2: the owner's declared directed edit, proven to fail at the exact base before it is admitted ---------------

DIRECTED_TEST = "from pkg.core import one\n\n\ndef test_one_is_two():\n    assert one() == 2\n"
DIRECTED = {"module_path": "pkg/core.py", "targets": ["one"], "test_path": "tests/test_core_one.py",
            "test_source": DIRECTED_TEST, "direction": "Change one() so that it returns 2 instead of 1; keep its name and signature."}


def directed_world(world: dict, spec: dict | None = None) -> dict:
    """The clone's graph declares one Level 2 slice carrying the owner's directed edit; HEAD moves to that commit."""
    payload = graph(level=2)
    payload["capabilities"][0]["slices"][0]["directed_edit"] = DIRECTED if spec is None else spec
    del payload["capabilities"][0]["slices"][0]["acceptance_examples"]
    (world["clone"] / SOURCE).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    run_git(world["clone"], "commit", "-q", "-am", "declare directed edit")
    world["head"] = run_git(world["clone"], "rev-parse", "HEAD")
    return world


def fake_tests(returncode: int, tail: str = "E   assert 1 == 2\nFAILED tests/test_core_one.py::test_one_is_two"):
    return lambda _tree, _test, _check: {"ok": returncode == 0, "returncode": returncode, "output_tail": tail}


class DirectedSpawner(Spawner):
    """Plays the draft-author process for a Level 2 request: no planner, the real validate_direct, a fake test run."""

    def __init__(self, execute) -> None:
        super().__init__()
        self.execute = execute

    def __call__(self, request_file: Path, clone: Path, base: str, result: Path) -> int:
        self.launched.append((request_file, clone, base, result))
        request = json.loads(request_file.read_text(encoding="utf-8"))
        declared = dict(request["directed_edit"])
        declared["direction"] = author.directed_direction(request, declared)
        author.run(request, clone, base, result, planner=lambda *_a: declared,
                   validator=author.directed_validator(WORKER, lambda: True, execute=self.execute),
                   authority=lambda: True, roots=["pkg/"], forbidden=[".github/", "engineering/", "docs/"], attempted=[])
        return 31337


@pytest.mark.parametrize("change,usable", [
    ({}, True),
    ({"module_path": ""}, False),
    ({"test_path": None}, False),
    ({"targets": []}, False),
    ({"targets": ["a", "b", "c", "d"]}, False),
    ({"targets": ["not an identifier"]}, False),
    ({"test_source": "def test_x(:\n"}, False),
    ({"test_source": "def helper():\n    assert True\n"}, False),  # no test function
    ({"test_source": "def test_x():\n    assert 1 == 2\n"}, False),  # never touches the named unit
    ({"test_source": "from pkg.core import one\n\n\ndef test_x():\n    one()\n"}, False),  # asserts nothing
])
def test_only_a_complete_directed_edit_whose_test_exercises_its_targets_is_usable(change, usable) -> None:
    request = {"route": {"builder": refill.LOCAL}, "capability_level": 2, "directed_edit": {**DIRECTED, **change}}

    assert (drafter.directed_spec(request) is not None) is usable
    assert drafter.eligible(request) is usable
    assert drafter.missing_directed_edit(request) is (not usable)


def test_a_level_two_request_not_routed_to_the_local_builder_is_never_drafted_locally() -> None:
    request = {"route": {"builder": refill.CAPABLE}, "capability_level": 2, "directed_edit": DIRECTED}
    assert drafter.eligible(request) is False and drafter.missing_directed_edit(request) is False


def test_a_level_two_slice_without_a_declared_edit_asks_for_it_and_starts_nothing(world: dict) -> None:
    directed_world(world, spec={})
    (request,) = refill_pass(world)["local_requests"]
    spawner = Spawner()

    outcomes = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    assert [o["status"] for o in outcomes] == ["NEEDS_ACCEPTANCE_EXAMPLES"] and "directed_edit" in outcomes[0]["reason"]
    assert spawner.launched == []


def test_a_proven_failing_directed_edit_becomes_a_dispatchable_level_two_item_through_the_unchanged_admission(
        world: dict) -> None:
    directed_world(world)
    (request,) = refill_pass(world)["local_requests"]
    spawner = DirectedSpawner(fake_tests(1))
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, may_start=False)

    report = refill_pass(world)

    assert report["admitted"] == [request["id"]], report
    (item,) = refill.admitted_items(world["lane"])
    assert item["level"] == 2 and item["approved_base"] == world["head"] and backlog.item_problems(item) == []
    assert item["direct"]["targets"] == ["one"] and item["direct"]["test_source"] == DIRECTED_TEST
    assert item["authorization"]["declared_capability_level"] == 2 and item["authorization"]["route"] == refill.LOCAL
    assert backlog.next_item([item], {}, REPO, max_level=2) == item  # the existing dispatcher accepts it
    assert backlog.edit_files(item) == {"pkg/core.py", "tests/test_core_one.py"}  # exactly the declared scope


@pytest.mark.parametrize("execute,reason", [
    (fake_tests(0, "1 passed"), "APPROVED_TEST_ALREADY_PASSES_AT_BASE"),  # approves nothing
    (fake_tests(2, "ERROR collecting tests/test_core_one.py"), "APPROVED_TEST_DOES_NOT_RUN_AT_BASE"),
    (fake_tests(1, "E   ModuleNotFoundError: No module named 'pkg.missing'"), "APPROVED_TEST_DOES_NOT_RUN_AT_BASE"),
    (fake_tests(5, "no tests ran"), "APPROVED_TEST_DOES_NOT_RUN_AT_BASE"),
])
def test_a_test_that_does_not_fail_for_the_right_reason_at_the_base_is_never_drafted(world: dict, execute, reason) -> None:
    directed_world(world)
    (request,) = refill_pass(world)["local_requests"]
    spawner = DirectedSpawner(execute)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    (outcome,) = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, may_start=False)

    assert outcome["status"] == "FAILED" and reason in outcome["reason"]
    assert not (world["lane"] / refill.DRAFTS / f"{request['id']}.json").exists()


@pytest.mark.parametrize("change,reason", [
    ({"targets": ["one", "missing_unit"]}, "DIRECT_UNITS_UNKNOWN"),
    ({"module_path": "pkg/absent.py"}, "DIRECT_MODULE_MISSING"),
    ({"test_path": "pkg/test_core_one.py"}, "DIRECT_TEST_PATH_INVALID"),
    ({"direction": "too short"}, "DIRECT_DIRECTION_INCOMPLETE"),
])
def test_the_workers_own_validator_still_rejects_an_unsound_declared_edit(world: dict, change, reason) -> None:
    directed_world(world, spec={**DIRECTED, **change})
    (request,) = refill_pass(world)["local_requests"]
    spawner = DirectedSpawner(fake_tests(1))
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    (outcome,) = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, may_start=False)

    assert outcome["status"] == "FAILED" and reason in outcome["reason"]


def test_a_rejected_directed_draft_is_not_retried_because_the_assembly_is_deterministic(world: dict) -> None:
    directed_world(world)
    (request,) = refill_pass(world)["local_requests"]
    spawner = DirectedSpawner(fake_tests(0, "1 passed"))
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, may_start=False)

    assert drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner) == []
    assert len(spawner.launched) == 1


def test_a_directed_draft_that_escapes_the_roadmap_scope_is_rejected_by_admission(world: dict) -> None:
    directed_world(world)
    (request,) = refill_pass(world)["local_requests"]
    request = {**request, "allowed_paths": ["pkg/other/**", "tests/**"]}  # the declared module lies outside the slice scope
    validated = WORKER.validate_direct(dict(DIRECTED), world["clone"], world["head"],
                                       author.build_packet(request, ["pkg/"], world["head"], [], []))
    draft = drafter.direct_to_draft(request, {**validated, "approved_base": world["head"]})

    problems = refill.draft_problems(draft, request, items=[], reserved=set(), head=world["head"],
                                     changed_since=lambda *_a: set(), max_level=2)

    assert "PATH_OUTSIDE_ROADMAP_SCOPE" in problems


def test_the_verification_checkout_belongs_to_the_lane_clone_so_its_installed_environment_is_reused(world: dict) -> None:
    """Review (#616): a standalone checkout made the worker rebuild every dependency for each directed draft."""
    tree = author.verification_worktree(world["clone"], world["head"])
    (tree / "tests").mkdir(exist_ok=True)
    (tree / "tests" / "test_leftover.py").write_text("x = 1\n", encoding="utf-8")

    common = run_git(tree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    assert Path(common).resolve() == (world["clone"] / ".git").resolve()  # the worker derives the environment from this
    again = author.verification_worktree(world["clone"], world["head"])

    assert again == tree and not (tree / "tests" / "test_leftover.py").exists()  # reused, never deleted, cleaned
    assert run_git(tree, "rev-parse", "HEAD") == world["head"]
    assert run_git(world["clone"], "worktree", "list").count("draft-check") == 1


def test_the_proven_base_travels_inside_the_directed_specification_the_worker_receives(world: dict) -> None:
    """Review (#616): `validate_direct` reads approved_base from the nested spec, so completion elsewhere is recognised."""
    directed_world(world)
    (request,) = refill_pass(world)["local_requests"]
    spawner = DirectedSpawner(fake_tests(1))
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, may_start=False)
    refill_pass(world)
    (item,) = refill.admitted_items(world["lane"])

    assert item["approved_base"] == world["head"] == item["direct"]["approved_base"]
    packet = author.build_packet(request, ["pkg/"], world["head"], [], [])
    assert WORKER.validate_direct(item["direct"], world["clone"], world["head"], packet)["approved_base"] == world["head"]

def test_no_second_draft_is_launched_once_a_valid_draft_has_been_admitted(world: dict) -> None:
    """2026-11-02 VoiceStudio S2: attempt 1 started the moment attempt 0 was admitted and spent 5 minutes of RAM."""
    request = request_of(world)
    spawner = Spawner(finish=True)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, may_start=False)
    assert refill_pass(world)["admitted"] == [request["id"]]
    launched = len(spawner.launched)

    again = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    assert again == [] and len(spawner.launched) == launched  # the approved request is not drafted again


def test_a_rejected_draft_is_still_retried_because_nothing_was_admitted(world: dict) -> None:
    request = request_of(world)
    drafts = world["lane"] / refill.DRAFTS
    drafts.mkdir(parents=True)
    (drafts / f"{request['id']}.json").write_text("{}", encoding="utf-8")
    import hashlib
    digest = hashlib.sha256((drafts / f"{request['id']}.json").read_bytes()).hexdigest()
    refill._mutate(world["lane"], lambda store: store["processed"].update({request["id"]: digest}))
    spawner = Spawner()

    assert drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)[0]["status"] == "STARTED"


def test_a_revision_may_reuse_its_own_failed_function_but_other_attempted_functions_stay_blocked(world: dict) -> None:
    """VoiceStudio S2-r1 (2026-11-02): PLAN_ALREADY_ATTEMPTED refused the very function the owner's roadmap names."""
    request = request_of(world)
    spawner = Spawner(finish=True)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner, may_start=False)
    assert refill_pass(world)["admitted"] == [request["id"]]
    (item,) = refill.admitted_items(world["lane"])
    assert item["build"]["function_name"] == "newest_entries"
    state = {"items": {item["id"]: {"attempts": 3, "outcome": "REVIEW_REQUIRED: CHANGES_OUTSIDE_PLAN: x.py"}}}

    report = refill.refill_repository(world["lane"], world["state_root"], REPO, world["clone"], world["head"],
                                      items=[item], state=state, reserved=set(), max_level=2)
    (revision,) = report["local_requests"]
    assert revision["id"] == item["id"] + "-r1" and revision["prior_failures"][0]["id"] == item["id"]
    recorder = Spawner()

    drafter.advance(world["lane"], [revision], world["clone"], world["head"], spawn=recorder,
                    extra={"attempted_functions": ["newest_entries", "something_else"]})

    sent = json.loads(recorder.launched[0][0].read_text(encoding="utf-8"))
    assert sent["attempted_functions"] == ["something_else"]  # only the function this slice itself tried is released


def test_a_first_request_keeps_every_attempted_function_blocked(world: dict) -> None:
    request = request_of(world)
    recorder = Spawner()

    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=recorder,
                    extra={"attempted_functions": ["newest_entries", "something_else"]})

    sent = json.loads(recorder.launched[0][0].read_text(encoding="utf-8"))
    assert sent["attempted_functions"] == ["newest_entries", "something_else"]


def _store_item(world: dict, ident: str, capability: str, name: str, outcome: str | None, repository: str = REPO) -> None:
    """One earlier lane item: what it built, which capability it addressed, and how it ended (None: still in flight)."""
    row = {"id": ident, "repository": repository, "addresses": f"{capability}: some-slice", "build": {"function_name": name}}
    refill._mutate(world["lane"], lambda store: store["items"].append(row))
    if outcome is not None:
        state = refill.backlog.load_state(world["lane"])
        state.setdefault("items", {})[ident] = {"attempts": 2, "outcome": outcome}
        refill.backlog.state_path(world["lane"]).write_text(json.dumps(state), encoding="utf-8")


def _sent_names(world: dict, request: dict, names: list[str]) -> list[str]:
    recorder = Spawner()
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=recorder, extra={"attempted_functions": names})
    return json.loads(recorder.launched[0][0].read_text(encoding="utf-8"))["attempted_functions"]


def test_a_replacement_slice_may_reuse_a_name_only_a_failed_slice_of_its_capability_tried(world: dict) -> None:
    """Rocksmith section-agreement-v2 (2026-11-14): a new slice id has no prior_failures, so the natural name was refused."""
    request = request_of(world)
    assert not request.get("prior_failures")
    _store_item(world, "RM-old", request["capability"], "section_agreement", "FAIL: SLICE_TESTS_FAILED")
    _store_item(world, "RM-old-r1", request["capability"], "section_agreement", "REVIEW_REQUIRED: CHANGES_OUTSIDE_PLAN")

    assert _sent_names(world, request, ["section_agreement", "something_else"]) == ["something_else"]


def test_a_merged_name_or_another_capabilitys_name_or_an_unfinished_items_name_stays_blocked(world: dict) -> None:
    request = request_of(world)
    capability = request["capability"]
    _store_item(world, "RM-shipped", capability, "shipped_fn", "FAIL: SLICE_TESTS_FAILED")
    _store_item(world, "RM-shipped-r1", "another-capability", "shipped_fn", "MERGED")  # the function exists in the repository
    _store_item(world, "RM-elsewhere", "another-capability", "elsewhere_fn", "FAIL: SLICE_TESTS_FAILED")
    _store_item(world, "RM-in-flight", capability, "in_flight_fn", None)
    _store_item(world, "RM-other-repo", capability, "other_repo_fn", "FAIL: SLICE_TESTS_FAILED", repository="jweter/other")
    names = ["elsewhere_fn", "in_flight_fn", "other_repo_fn", "shipped_fn"]

    assert _sent_names(world, request, names) == names


@pytest.mark.parametrize("other_capability, other_outcome", [
    ("another-capability", "FAIL: SLICE_TESTS_FAILED"),  # another capability also failed with it
    (None, None),                                          # an item of this capability is still building it
    ("another-capability", "MERGED"),                    # it shipped elsewhere in the repository
])
def test_a_name_shared_with_any_use_that_is_not_abandoned_here_stays_blocked(world: dict, other_capability, other_outcome) -> None:
    """Codex P2 on #699: one failed use in this capability must not release a name that something else also holds."""
    request = request_of(world)
    _store_item(world, "RM-failed-here", request["capability"], "same_fn", "FAIL: SLICE_TESTS_FAILED")
    _store_item(world, "RM-other-use", other_capability or request["capability"], "same_fn", other_outcome)

    assert _sent_names(world, request, ["same_fn"]) == ["same_fn"]


def test_an_unreadable_outcome_ledger_releases_no_name(world: dict) -> None:
    request = request_of(world)
    _store_item(world, "RM-old", request["capability"], "section_agreement", "FAIL: SLICE_TESTS_FAILED")
    refill.backlog.state_path(world["lane"]).write_text("{not json", encoding="utf-8")

    assert _sent_names(world, request, ["section_agreement"]) == ["section_agreement"]


def _finish_pending(world: dict, reason: str) -> None:
    for job in (Path(world["lane"]) / drafter.JOBS).glob("*.json"):
        row = json.loads(job.read_text(encoding="utf-8"))
        if row["stage"] == "DRAFTING":
            result = Path(row["result"])
            result.parent.mkdir(parents=True, exist_ok=True)
            result.write_text(json.dumps({"ok": False, "reason": reason}), encoding="utf-8")


NAME_REFUSED = ("PlanRejected: PLAN_ALREADY_ATTEMPTED: {} was already tried in this repository; "
                "choose a different function")  # the worker's exact text, as recorded on 2026-11-14


def _refuse_twice(world: dict, request: dict, spawner: Spawner, name: str) -> None:
    for _ in range(drafter.MAX_ATTEMPTS):
        drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
        _finish_pending(world, NAME_REFUSED.format(name))
        drafter.harvest(world["lane"])


def test_a_draft_refused_only_for_a_since_released_name_does_not_spend_an_attempt(world: dict) -> None:
    """Rocksmith section-agreement-v2 (2026-11-14): both attempts went to the name guard before the builder ever ran."""
    from project_orchestrator import iron_god_task_decomposer as decomposer

    request, spawner = request_of(world), Spawner()
    _refuse_twice(world, request, spawner, "section_agreement")
    jobs = Path(world["lane"]) / drafter.JOBS
    before = {p.name: p.read_bytes() for p in jobs.glob("*.json")}
    assert decomposer.drafting_exhausted(world["lane"], request["id"])  # nothing releases the name yet: both are spent
    assert drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner) == []

    _store_item(world, "RM-old", request["capability"], "section_agreement", "FAIL: SLICE_TESTS_FAILED")

    assert not decomposer.drafting_exhausted(world["lane"], request["id"])
    assert decomposer.local_work_waiting(world["lane"], [request])
    (started,) = drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    assert started["status"] == "STARTED" and started["attempt"] == 2 and len(spawner.launched) == 3
    assert {name: (jobs / name).read_bytes() for name in before} == before  # the spent records are never rewritten


def test_the_released_name_gets_the_normal_two_attempts_and_no_more(world: dict) -> None:
    request, spawner = request_of(world), Spawner()
    _refuse_twice(world, request, spawner, "section_agreement")
    _store_item(world, "RM-old", request["capability"], "section_agreement", "FAIL: SLICE_TESTS_FAILED")
    for _ in range(drafter.MAX_ATTEMPTS):
        drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
        _finish_pending(world, "PlanRejected: a real failure")
        drafter.harvest(world["lane"])

    assert drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner) == []
    assert len(spawner.launched) == 2 * drafter.MAX_ATTEMPTS


@pytest.mark.parametrize("capability, outcome", [
    ("another-capability", "FAIL: SLICE_TESTS_FAILED"),  # the name belongs to another capability
    (None, "MERGED"),                                      # the function exists
    (None, None),                                          # an item is still building it
])
def test_a_refusal_for_a_name_that_is_still_blocked_stays_spent(world: dict, capability, outcome) -> None:
    request, spawner = request_of(world), Spawner()
    _refuse_twice(world, request, spawner, "held_fn")
    _store_item(world, "RM-holder", capability or request["capability"], "held_fn", outcome)

    assert drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner) == []
    assert len(spawner.launched) == drafter.MAX_ATTEMPTS


def test_a_name_refusal_whose_stored_request_is_unreadable_stays_spent(world: dict) -> None:
    request, spawner = request_of(world), Spawner()
    _refuse_twice(world, request, spawner, "section_agreement")
    _store_item(world, "RM-old", request["capability"], "section_agreement", "FAIL: SLICE_TESTS_FAILED")
    for copy in (Path(world["lane"]) / drafter.REQUEST_COPIES).glob("*.json"):
        copy.write_text("{not json", encoding="utf-8")

    assert drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner) == []


def test_an_owner_takeover_mid_draft_does_not_spend_an_attempt_and_keeps_its_record(world: dict) -> None:
    """Owner takeover is routine (2026-11-02: it consumed S2-r1's first of two attempts)."""
    request = request_of(world)
    spawner = Spawner()
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    _finish_pending(world, "PlanRejected: a real failure")  # attempt 0 genuinely fails
    drafter.harvest(world["lane"])
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    _finish_pending(world, "RuntimeError: EXECUTION_AUTHORITY_REVOKED")  # attempt 1 is cut short by the owner
    (outcome,) = drafter.harvest(world["lane"])
    assert outcome["status"] == "INTERRUPTED"

    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)  # authority is back

    stages = sorted(json.loads(p.read_text(encoding="utf-8"))["stage"] for p in (Path(world["lane"]) / drafter.JOBS).glob("*.json"))
    assert stages == ["DRAFTING", "FAILED", "INTERRUPTED"] and len(spawner.launched) == 3  # the record is kept


def test_real_failures_still_spend_attempts_and_interruptions_are_bounded(world: dict) -> None:
    request = request_of(world)
    spawner = Spawner()
    for _ in range(drafter.MAX_ATTEMPTS):
        drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
        for job in (Path(world["lane"]) / drafter.JOBS).glob("*.json"):
            row = json.loads(job.read_text(encoding="utf-8"))
            if row["stage"] == "DRAFTING":
                Path(row["result"]).parent.mkdir(parents=True, exist_ok=True)
                Path(row["result"]).write_text(json.dumps({"ok": False, "reason": "PlanRejected: nope"}), encoding="utf-8")
        drafter.harvest(world["lane"])
    before = len(spawner.launched)
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    assert len(spawner.launched) == before == drafter.MAX_ATTEMPTS  # two genuine failures: no third attempt


def test_a_legacy_failed_record_that_was_really_a_takeover_does_not_spend_an_attempt(world: dict) -> None:
    """S2-r1 a0 was written as FAILED by the release before INTERRUPTED existed; no runtime file may need editing."""
    request = request_of(world)
    legacy = Path(world["lane"]) / drafter.JOBS / f"{request['id']}.a0.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"id": request["id"], "attempt": 0, "stage": "FAILED",
                                  "reason": "RuntimeError: EXECUTION_AUTHORITY_REVOKED"}), encoding="utf-8")
    spawner = Spawner()

    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)

    assert len(spawner.launched) == 1 and legacy.is_file()  # a fresh attempt a1, the old record untouched


def _app_dir_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repo shaped like VoiceStudio: backend/ is an app dir (not a package) and tests/backend/ mirrors it."""
    import subprocess

    repo = tmp_path / "appdir"
    for rel in ("backend/services/__init__.py", "tests/backend/__init__.py", "tests/backend/services/__init__.py"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    return repo, head


def test_an_app_dir_repository_gets_the_roadmaps_test_path_and_its_real_import_root(tmp_path: Path) -> None:
    """VoiceStudio S2-r1 (2026-11-02): the worker wrote tests/test_x.py importing backend.services.x - uncollectable."""
    repo, head = _app_dir_repo(tmp_path)
    packet = {"paths": ["backend/", "tests/"], "planning": {"forbidden_paths": []}, "attempted_functions": [],
              "declared_tests": ["tests/backend/services/test_singing_project.py", "docs/x.md"]}
    plan = {"function_name": "singing_project_paths", "signature": "def singing_project_paths(project_id: str) -> dict:",
            "module_path": "backend/services/singing_project.py", "objective": "Return the layout paths.",
            "examples": [{"call": "singing_project_paths('a')", "why": "valid", "expected": {"a": 1}},
                         {"call": "singing_project_paths('')", "why": "empty", "expected": {}},
                         {"call": "singing_project_paths('..')", "why": "dots", "expected": {}}]}

    validated = WORKER.validate_plan(plan, repo, head, packet)

    assert validated["test_path"] == "tests/backend/services/test_singing_project.py"
    assert validated["import_path"] == "services.singing_project"


def test_an_ordinary_package_repository_keeps_the_default_test_path_and_dotted_import(world: dict) -> None:
    packet = author.build_packet(request_of(world), ["pkg/"], world["head"], [".github/"], [])

    validated = WORKER.validate_plan(GOOD_PLAN, world["clone"], world["head"], packet)

    assert validated["test_path"].startswith("tests/test_") and validated["import_path"].startswith("pkg.")


def test_standalone_evaluation_uses_the_app_dir_import_root(tmp_path: Path) -> None:
    """Review P2 on #627: actual_result() runs without pytest's conftest path setup."""
    repo, head = _app_dir_repo(tmp_path)
    (repo / "backend" / "services" / "demo.py").write_text("def demo(x):\n    return {'x': x}\n", encoding="utf-8")
    plan = {"module_path": "backend/services/demo.py", "function_name": "demo", "import_path": "services.demo"}

    assert WORKER.actual_result(repo, plan, "demo(1)") == "{'x': 1}"
    ordinary = {"module_path": "backend/services/demo.py", "function_name": "demo", "import_path": "backend.services.demo"}
    assert WORKER.actual_result(repo, ordinary, "demo(1)") == "{'x': 1}"  # the default form still resolves


# --- 2026-11-13: the builder must see the roadmap's rule, not its first 160 characters ------------------------------

ARRANGEMENT_OBJECTIVE = (
    "Issue #693 slice 5 lists arrangement presence and Bass/Lead/Rhythm symmetry among the differential metrics the comparator "
    "does not yet have (docs/private-rocksmith-library-regression-corpus.md, Differential metrics). Add one pure helper, in a "
    "new module of its own, that takes the arrangement names found in a package and returns a dict with the boolean keys bass, "
    "lead and rhythm and a boolean key symmetric that is true only when all three are present.")


def test_the_roadmap_rule_reaches_the_builder_even_when_background_comes_first() -> None:
    """The real failed slice: its rule sat after 160 characters of background and was cut off."""
    assert "true only when all three are present" not in ARRANGEMENT_OBJECTIVE[:160]

    objective = drafter.build_objective("Report which arrangements are present and whether they are symmetric.",
                                        ARRANGEMENT_OBJECTIVE)

    assert "symmetric that is true only when all three are present" in objective
    assert len(objective) <= drafter.BUILD_OBJECTIVE_CHARS


def test_the_roadmap_goal_wins_the_room_and_the_planner_restatement_is_kept_only_whole() -> None:
    long_goal = "Return x. " + "r" * 700
    own = "The planner's own restatement of the goal."

    crowded = drafter.build_objective(own, long_goal)
    roomy = drafter.build_objective(own, "Return the newest entries.")

    assert crowded.startswith(drafter.ROADMAP_GOAL_PREFIX) and own not in crowded  # never half a sentence of the planner's
    assert len(crowded) == drafter.BUILD_OBJECTIVE_CHARS
    assert roomy == f"{own} {drafter.ROADMAP_GOAL_PREFIX}Return the newest entries."
    assert drafter.build_objective("", "Return x.") == f"{drafter.ROADMAP_GOAL_PREFIX}Return x."


def test_the_build_spec_carries_the_slice_earlier_failures_for_the_builder(world: dict) -> None:
    request = request_of(world, prior_failures=[
        {"id": "RM-a", "outcome": "FAIL: SLICE_TESTS_FAILED", "evidence": "TypeError: unhashable type: 'list'"},
        {"id": "RM-a-r1", "outcome": "FAIL: SLICE_TESTS_FAILED", "evidence": "TypeError: unhashable type: 'list'"},
        {"id": "RM-a-r2", "outcome": "FAIL: SLICE_TESTS_FAILED"}])

    draft = drafter.plan_to_draft(request, GOOD_PLAN, validated_good(world, request))

    assert draft["build"]["avoid"] == ["TypeError: unhashable type: 'list'"]
    assert drafter.plan_to_draft(request_of(world), GOOD_PLAN, validated_good(world, request_of(world)))["build"]["avoid"] == []


def test_the_builder_is_shown_the_newest_failures_when_a_slice_has_failed_in_more_ways_than_fit(world: dict) -> None:
    """Review (Codex P2, ChatGPT seq 350): on a third re-authoring the newest error must not be the one dropped."""
    request = request_of(world, prior_failures=[
        {"id": "RM-a", "outcome": "FAIL", "evidence": "TypeError: first"},
        {"id": "RM-a-r1", "outcome": "FAIL", "evidence": "ValueError: second"},
        {"id": "RM-a-r2", "outcome": "FAIL", "evidence": "KeyError: newest"}])

    draft = drafter.plan_to_draft(request, GOOD_PLAN, validated_good(world, request))

    assert draft["build"]["avoid"] == ["ValueError: second", "KeyError: newest"]
