"""The local task decomposer: deterministic acceptance of a model-proposed Level 1 plan, and its pipeline wiring."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from project_orchestrator import iron_god_calibration as calibration
from project_orchestrator import iron_god_local_drafter as drafter
from project_orchestrator import iron_god_roadmap_progress as progress
from project_orchestrator import iron_god_roadmap_refill as refill
from project_orchestrator import iron_god_task_decomposer as td

REPO = "jweter/VoiceStudio"
SOURCE = "engineering/capability-graph.json"

PARENT = {
    "id": refill.item_id(REPO, "cap-a", "big"),
    "repository": REPO,
    "roadmap_ref": f"{SOURCE}#cap-a/big",
    "capability": "cap-a",
    "slice": "big",
    "objective": "Add text helpers: slugify a title and clamp a count to a range.",
    "slice_acceptance": [
        "slugify lowercases and joins words with hyphens",
        "clamp keeps a value inside the range",
        "Canonical preflight, required CI checks and review pass at the exact head",
    ],
    "allowed_paths": ["pkg/**", "tests/**"],
    "forbidden_paths": ["pkg/secret/**"],
    "capability_level": 4,
    "route": {"builder": "CAPABLE", "reason": "SLICE_EXCEEDS_CALIBRATED_CAPABILITY"},
    "exact_head": "a" * 40,
    "graph_path": SOURCE,
}

GOOD = {
    "subtasks": [
        {
            "key": "t1",
            "title": "slugify",
            "depends_on": [],
            "module_path": "pkg/slugify.py",
            "function_name": "slugify",
            "signature": "def slugify(title: str) -> str:",
            "done_when": "slugify lowercases and hyphenates",
            "acceptance_examples": [
                {
                    "call": "{fn}('Hello World')",
                    "expected": "hello-world",
                    "why": "words are hyphenated",
                },
                {
                    "call": "{fn}('A')",
                    "expected": "a",
                    "why": "a single word is lowercased",
                },
            ],
        },
        {
            "key": "t2",
            "title": "clamp",
            "depends_on": ["t1"],
            "module_path": "pkg/clamp.py",
            "function_name": "clamp",
            "signature": "def clamp(value: int, low: int, high: int) -> int:",
            "done_when": "clamp keeps a value in range",
            "acceptance_examples": [
                {
                    "call": "{fn}(5, 0, 3)",
                    "expected": 3,
                    "why": "above the range returns the high bound",
                },
                {
                    "call": "{fn}(-1, 0, 3)",
                    "expected": 0,
                    "why": "below the range returns the low bound",
                },
            ],
        },
    ],
    "coverage": {"1": ["t1"], "2": ["t2"]},
    "residual": [],
}


def check(raw: Any, request: dict | None = None, exists=lambda _p: False):
    return td.validate_plan(raw, request or PARENT, exists=exists)


def mutated(change) -> dict:
    plan = copy.deepcopy(GOOD)
    change(plan)
    return plan


def test_a_covering_level_one_plan_is_accepted_and_gate_lines_need_no_subtask() -> None:
    plan, problems = check(GOOD)
    assert problems == [] and plan is not None
    assert [s["key"] for s in plan["subtasks"]] == ["t1", "t2"] and plan[
        "oracle"
    ] == "SELF_AUTHORED"
    assert plan["coverage"] == {
        "1": ["t1"],
        "2": ["t2"],
    }  # criterion 3 is a gate, inherited by every subtask


@pytest.mark.parametrize(
    "change, fragment",
    [
        (lambda p: p.update(residual=["talks to a subprocess"]), "RESIDUAL_NOT_LOCAL"),
        (
            lambda p: p["subtasks"][0].update(module_path="docs/slugify.py"),
            "outside the slice's allowed paths",
        ),
        (
            lambda p: p["subtasks"][0].update(module_path="pkg/secret/slugify.py"),
            "outside the slice's allowed paths",
        ),
        (
            lambda p: p["subtasks"][0].update(module_path="../pkg/slugify.py"),
            "relative POSIX",
        ),
        (
            lambda p: p["subtasks"][1].update(module_path="pkg/slugify.py"),
            "used by another subtask",
        ),
        (lambda p: p["subtasks"][1].update(depends_on=["t3"]), "EARLIER"),
        (lambda p: p["subtasks"][0].update(depends_on=["t2"]), "EARLIER"),
        (
            lambda p: p["subtasks"][0].update(
                signature="def other(title: str) -> str:"
            ),
            "signature must be",
        ),
        (
            lambda p: p["subtasks"][0].update(
                function_name="_hidden", signature="def _hidden(t: str) -> str:"
            ),
            "public function",
        ),
        (lambda p: p["subtasks"][0]["acceptance_examples"].pop(), "at least 2"),
        (
            lambda p: p["subtasks"][0]["acceptance_examples"][0].update(
                call="{fn}(open('x'))"
            ),
            "literal",
        ),
        (
            lambda p: p["subtasks"][0]["acceptance_examples"][0].update(
                call="{fn}('a', 'b')"
            ),
            "does not bind",
        ),
        (
            lambda p: p["subtasks"][0]["acceptance_examples"].append(
                {
                    "call": "{fn}('A')",
                    "expected": "z",
                    "why": "contradicts the other example",
                }
            ),
            "contradictory",
        ),
        (lambda p: p["coverage"].pop("2"), "criterion 2 is not covered"),
        (lambda p: p["coverage"].update({"2": ["t9"]}), "criterion 2 is not covered"),
        (
            lambda p: p["coverage"].update({"2": ["t1"]}),
            "t2 delivers no acceptance criterion",
        ),
        (lambda p: p.update(subtasks=p["subtasks"] * 3), "1..5"),
        (lambda p: p.update(subtasks=[]), "1..5"),
    ],
)
def test_a_plan_that_breaks_a_rule_is_rejected_with_the_reason(
    change, fragment: str
) -> None:
    plan, problems = check(mutated(change))
    assert plan is None and any(fragment in problem for problem in problems), problems


def test_a_subtask_may_only_add_a_new_module() -> None:
    plan, problems = check(GOOD, exists=lambda path: path == "pkg/clamp.py")
    assert plan is None and any("already exists" in p for p in problems)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        "plan",
        {"subtasks": "x"},
        {"subtasks": [1], "coverage": {}, "residual": []},
    ],
)
def test_garbage_is_rejected_not_raised(raw: Any) -> None:
    plan, problems = check(raw)
    assert plan is None and problems


def test_only_sized_out_authorized_slices_are_decomposable() -> None:
    assert td.decomposable(PARENT)
    assert not td.decomposable({**PARENT, "capability_level": 1})
    assert not td.decomposable({**PARENT, "capability_level": None})
    assert not td.decomposable({**PARENT, "route": {"builder": "LOCAL"}})
    assert not td.decomposable({**PARENT, "route": {"builder": "NONE"}})
    assert not td.decomposable({**PARENT, "force_builder": "CAPABLE"})
    assert not td.decomposable({**PARENT, "slice": refill.PLANNING_SLICE})
    assert not td.decomposable(
        {**PARENT, "id": PARENT["id"] + refill.ESCALATION_SUFFIX}
    )
    assert not td.decomposable({**PARENT, "slice_acceptance": []})


def test_the_second_attempt_is_told_why_the_first_was_rejected_and_a_dead_model_is_a_blocker() -> (
    None
):
    prompts: list[str] = []
    answers = iter([{"subtasks": [], "coverage": {}, "residual": []}, GOOD])

    def ask(prompt: str) -> Any:
        prompts.append(prompt)
        return next(answers)

    result = td.propose(
        PARENT, "existing python files: pkg/core.py", ask, exists=lambda _p: False
    )
    assert (
        result["ok"]
        and len(prompts) == 2
        and "was rejected" not in prompts[0]
        and "1..5" in prompts[1]
    )
    assert "pkg/**" in prompts[0] and "pkg/secret/**" in prompts[0]

    def dead(_prompt: str) -> Any:
        raise ConnectionRefusedError("ollama is down")

    gone = td.propose(PARENT, "", dead, exists=lambda _p: False)
    assert not gone["ok"] and gone["reason"].startswith("MODEL_UNAVAILABLE")
    bad = td.propose(PARENT, "", lambda _p: {"nope": 1}, exists=lambda _p: False)
    assert (
        not bad["ok"]
        and bad["reason"].startswith("PLAN_REJECTED")
        and len(bad["trail"]) == 2
    )


# ---- the job lifecycle ------------------------------------------------------------------------------------------------


class Spawner:
    def __init__(self, result: Any = None) -> None:
        self.result, self.launched = result, []

    def __call__(self, request_file: Path, clone: Path, base: str, out: Path) -> int:
        self.launched.append(out)
        if self.result is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(self.result), encoding="utf-8")
        return 4242


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    tree = tmp_path / "clone"
    (tree / "pkg").mkdir(parents=True)
    (tree / "pkg" / "core.py").write_text(
        "def one():\n    return 1\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(tree)], check=True)
    for args in (
        ("config", "user.email", "o@example.com"),
        ("config", "user.name", "Owner"),
        ("add", "."),
        ("commit", "-q", "-m", "seed"),
    ):
        subprocess.run(["git", "-C", str(tree), *args], check=True, capture_output=True)
    return tree


def head_of(tree: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_a_finished_job_is_revalidated_here_and_becomes_an_active_plan(
    tmp_path: Path, clone: Path
) -> None:
    lane, base, clock = tmp_path / "lane", head_of(clone), [1000.0]
    spawn = Spawner({"ok": True, "raw": GOOD, "model": "qwen2.5-coder:7b"})
    out = td.advance(lane, [PARENT], clone, base, spawn=spawn, now=lambda: clock[0])
    assert out == [
        {"id": PARENT["id"], "status": "STARTED", "attempt": 0, "repository": REPO}
    ]
    assert td.planning_in_progress(lane, now=1001.0)
    out = td.harvest(lane, now=lambda: 1002.0)
    assert out == [{"id": PARENT["id"], "status": "PLANNED", "subtasks": 2}]
    plan = json.loads(td.plan_path(lane, PARENT["id"]).read_text(encoding="utf-8"))
    assert (
        plan["status"] == "ACTIVE"
        and plan["planner"] == "qwen2.5-coder:7b"
        and plan["oracle"] == "SELF_AUTHORED"
    )
    assert td.active_parent_ids(lane) == {PARENT["id"]} and not td.planning_in_progress(
        lane, now=1003.0
    )
    assert td.children_of(lane, PARENT["id"]) == [
        PARENT["id"] + "-t1",
        PARENT["id"] + "-t2",
    ]


def test_a_plan_the_model_vouches_for_is_still_rejected_when_it_fails_validation_here(
    tmp_path: Path, clone: Path
) -> None:
    lane, base = tmp_path / "lane", head_of(clone)
    bad = mutated(
        lambda p: p["subtasks"][0].update(module_path="pkg/core.py")
    )  # exists in the clone
    td.advance(lane, [PARENT], clone, base, spawn=Spawner({"ok": True, "raw": bad}))
    (outcome,) = td.harvest(lane)
    assert (
        outcome["status"] == "FAILED"
        and outcome["reason"] == "PLAN_REJECTED"
        and not td.plan_path(lane, PARENT["id"]).exists()
    )


def test_two_failed_attempts_block_the_slice_with_the_exact_reason_and_it_stays_with_the_stronger_builder(
    tmp_path: Path, clone: Path
) -> None:
    lane, base = tmp_path / "lane", head_of(clone)
    for _ in range(2):
        td.advance(
            lane,
            [PARENT],
            clone,
            base,
            spawn=Spawner(
                {"ok": False, "reason": "PLAN_REJECTED: RESIDUAL_NOT_LOCAL: x"}
            ),
        )
        td.harvest(lane)
    out = td.advance(lane, [PARENT], clone, base, spawn=Spawner())
    assert out[0]["status"] == "BLOCKED" and "RESIDUAL_NOT_LOCAL" in out[0]["reason"]
    plan = json.loads(td.plan_path(lane, PARENT["id"]).read_text(encoding="utf-8"))
    assert (
        plan["status"] == "BLOCKED"
        and "DECOMPOSITION_ATTEMPTS_EXHAUSTED" in plan["blocker"]
    )
    assert (
        td.active_parent_ids(lane) == frozenset()
    )  # not held: the stronger builder may take it
    spawner = Spawner()
    assert (
        td.advance(lane, [PARENT], clone, base, spawn=spawner) == []
        and spawner.launched == []
    )  # never retried


def test_an_owner_takeover_never_spends_an_attempt_and_one_job_runs_at_a_time(
    tmp_path: Path, clone: Path
) -> None:
    lane, base = tmp_path / "lane", head_of(clone)
    other = {**PARENT, "id": PARENT["id"] + "x"}
    spawner = Spawner(
        {"ok": False, "reason": "RuntimeError: EXECUTION_AUTHORITY_REVOKED"}
    )
    pending = Spawner()  # a job that has not finished yet
    td.advance(lane, [PARENT, other], clone, base, spawn=pending)
    assert len(pending.launched) == 1
    assert (
        td.advance(lane, [PARENT, other], clone, base, spawn=pending) == []
        and len(pending.launched) == 1
    )
    for path in (lane / td.JOBS).glob("*.json"):
        path.unlink()
    td.advance(lane, [PARENT, other], clone, base, spawn=spawner)
    assert len(spawner.launched) == 1
    (outcome,) = td.harvest(lane)
    assert outcome["status"] == "INTERRUPTED"
    for _ in range(
        3
    ):  # three interruptions in a row: still a full budget of real attempts left
        td.advance(
            lane,
            [PARENT],
            clone,
            base,
            spawn=Spawner({"ok": False, "reason": "EXECUTION_AUTHORITY_REVOKED"}),
        )
        td.harvest(lane)
    assert not td.plan_path(lane, PARENT["id"]).exists()


def test_a_hung_job_is_failed_by_age_and_the_lease_is_returned(
    tmp_path: Path, clone: Path
) -> None:
    lane, base, released = tmp_path / "lane", head_of(clone), []
    td.advance(
        lane,
        [PARENT],
        clone,
        base,
        spawn=Spawner(),
        now=lambda: 100.0,
        reserve=lambda: "lease-1",
        release=released.append,
    )
    assert td.harvest(lane, now=lambda: 100.0 + 60, release=released.append) == []
    (outcome,) = td.harvest(
        lane, now=lambda: 100.0 + td.JOB_TIMEOUT_S + 1, release=released.append
    )
    assert outcome["reason"] == "PLANNING_TIMED_OUT" and released == ["lease-1"]


def test_nothing_starts_without_the_model_lease_or_while_a_worker_is_active(
    tmp_path: Path, clone: Path
) -> None:
    lane, base, spawner = tmp_path / "lane", head_of(clone), Spawner()
    assert td.advance(lane, [PARENT], clone, base, spawn=spawner, may_start=False) == []
    assert (
        td.advance(lane, [PARENT], clone, base, spawn=spawner, reserve=lambda: None)
        == []
    )
    assert spawner.launched == []


# ---- subtasks as ordinary pipeline requests ----------------------------------------------------------------------------


def active_plan(lane: Path) -> dict:
    return td.write_plan(
        lane,
        PARENT,
        td.validate_plan(GOOD, PARENT, exists=lambda _p: False)[0],
        planner="qwen2.5-coder:7b",
    )


def test_ready_subtasks_become_narrow_level_one_requests_that_respect_dependencies(
    tmp_path: Path,
) -> None:
    lane = tmp_path / "lane"
    active_plan(lane)
    (first,) = td.child_requests(lane, REPO, "b" * 40, [], {})
    assert (
        first["id"] == PARENT["id"] + "-t1"
        and first["capability_level"] == 1
        and first["exact_head"] == "b" * 40
    )
    assert first["allowed_paths"] == ["pkg/slugify.py", "tests/**"] and first[
        "forbidden_paths"
    ] == ["pkg/secret/**"]
    assert (
        first["slice"] == "big.t1"
        and first["roadmap_ref"] == f"{SOURCE}#cap-a/big.t1"
        and first["capability"] == "cap-a"
    )
    assert (
        len(drafter.roadmap_examples(first)) == 2
        and first["decomposition"]["oracle"] == "SELF_AUTHORED"
    )
    first["route"] = {"builder": refill.LOCAL}
    assert drafter.eligible(first)  # the unchanged local drafter takes it
    assert td.child_requests(lane, "someone/else", "b" * 40, [], {}) == []
    done = {"id": first["id"], "repository": REPO, "status": "approved"}
    (second,) = td.child_requests(
        lane, REPO, "b" * 40, [done], {"items": {first["id"]: {"outcome": "MERGED"}}}
    )
    assert (
        second["id"] == PARENT["id"] + "-t2"
        and second["allowed_paths"][0] == "pkg/clamp.py"
    )


def test_a_failed_subtask_is_reauthored_by_the_existing_revision_rules_and_an_exhausted_one_blocks_the_slice(
    tmp_path: Path,
) -> None:
    lane = tmp_path / "lane"
    active_plan(lane)
    t1 = PARENT["id"] + "-t1"
    failed = {"id": t1, "repository": REPO, "status": "approved", "level": 1}
    state = {"items": {t1: {"outcome": "FAIL", "attempts": 3}}}
    (retry,) = td.child_requests(lane, REPO, "b" * 40, [failed], state)
    assert retry["id"] == t1 + "-r1" and retry["prior_failures"][0]["id"] == t1
    chain = [
        failed,
        {**failed, "id": t1 + "-r1"},
        {**failed, "id": t1 + "-r2"},
        {**failed, "id": t1 + "-r3"},
    ]
    state = {"items": {i["id"]: {"outcome": "FAIL", "attempts": 3} for i in chain}}
    (change,) = td.settle(lane, chain, state)
    assert change["status"] == "BLOCKED" and "SUBTASK_EXHAUSTED" in change["blocker"]
    assert (
        td.active_parent_ids(lane) == frozenset()
        and td.child_requests(lane, REPO, "b" * 40, chain, state) == []
    )


def test_the_slice_is_complete_only_when_every_subtask_is_merged_and_the_graph_gets_their_evidence(
    tmp_path: Path,
) -> None:
    lane = tmp_path / "lane"
    active_plan(lane)
    t1, t2 = PARENT["id"] + "-t1", PARENT["id"] + "-t2"
    items = [{"id": t, "repository": REPO, "status": "approved"} for t in (t1, t2)]
    state = {"items": {t1: {"outcome": "MERGED", "packets": ["p1"]}}}
    assert td.settle(lane, items, state) == [] and td.active_parent_ids(lane) == {
        PARENT["id"]
    }
    state["items"][t2] = {"outcome": "MERGED", "packets": ["p2"]}
    (change,) = td.settle(lane, items, state)
    assert change["status"] == "DONE" and td.done_parent_ids(lane) == {PARENT["id"]}
    assert td.settle(lane, items, state) == []  # idempotent
    publications = {
        p: {
            "state": "MERGED",
            "pr": {"url": f"https://github.com/{REPO}/pull/{n}"},
            "merged_sha": "c" * 40,
        }
        for p, n in (("p1", 1), ("p2", 2))
    }
    graph = {
        "capabilities": [{"id": "cap-a", "slices": [{"id": "big", "state": "PLANNED"}]}]
    }
    found = progress.completed_slices(REPO, graph, items, state, publications, lane)
    assert [(f["capability"], f["slice"]) for f in found] == [("cap-a", "big")]
    assert any("pull/1" in e for e in found[0]["evidence"]) and any(
        "pull/2" in e for e in found[0]["evidence"]
    )
    assert all("subtask" in e for e in found[0]["evidence"])


# ---- end to end through the unchanged refill ----------------------------------------------------------------------------


def graph(level: int) -> dict:
    return {
        "schema_version": 1,
        "repository": REPO,
        "roadmap": ["cap-a"],
        "capabilities": [
            {
                "id": "cap-a",
                "capability": "Cap A",
                "state": "PLANNED",
                "depends_on": [],
                "completion_evidence": ["docs/x.md"],
                "slices": [
                    {
                        "id": "big",
                        "state": "PLANNED",
                        "capability_level": level,
                        "objective": PARENT["objective"],
                        "acceptance": PARENT["slice_acceptance"],
                        "allowed_paths": ["pkg/**", "tests/**"],
                        "forbidden_paths": ["pkg/secret/**"],
                    }
                ],
            }
        ],
    }


@pytest.fixture
def world(tmp_path: Path, clone: Path):
    (clone / "engineering").mkdir()
    (clone / SOURCE).write_text(
        json.dumps(graph(4), indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (clone / "engineering" / "control-plane.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "capability_graph": SOURCE,
                "authoritative_documents": [SOURCE],
            }
        ),
        encoding="utf-8",
    )
    for args in (("add", "."), ("commit", "-q", "-m", "graph")):
        subprocess.run(
            ["git", "-C", str(clone), *args], check=True, capture_output=True
        )
    lane, state_root = tmp_path / "lane", tmp_path / "flow-state"
    lane.mkdir()
    state_root.mkdir()
    calibration.profile_path(state_root).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ladder_version": calibration.LADDER_VERSION,
                "model": calibration.DEFAULT_MODEL,
                "hardware": calibration.hardware_fingerprint(),
                "max_passing_level": 2,
                "levels": {},
            }
        ),
        encoding="utf-8",
    )
    return {
        "clone": clone,
        "head": head_of(clone),
        "lane": lane,
        "state_root": state_root,
    }


def refill_pass(world: dict, items=None, state=None) -> dict:
    return refill.refill_repository(
        world["lane"],
        world["state_root"],
        REPO,
        world["clone"],
        world["head"],
        items=items or [],
        state=state or {},
        reserved=set(),
        max_level=2,
    )


def test_a_level_four_slice_flows_through_decomposition_into_local_requests_and_back_to_completion(
    world: dict,
) -> None:
    first = refill_pass(world)
    (parent,) = first["capable_requests"]
    assert first["local_requests"] == [] and td.decomposable(parent)

    spawn = Spawner({"ok": True, "raw": GOOD, "model": "qwen2.5-coder:7b"})
    td.advance(
        world["lane"],
        first["capable_requests"],
        world["clone"],
        world["head"],
        spawn=spawn,
    )
    assert td.harvest(world["lane"])[0]["status"] == "PLANNED"

    second = refill_pass(
        world
    )  # the parent is held; its first subtask is now an ordinary LOCAL request
    assert second["capable_requests"] == [] and [
        r["id"] for r in second["local_requests"]
    ] == [parent["id"] + "-t1"]
    child = second["local_requests"][0]
    assert child["route"]["builder"] == refill.LOCAL and drafter.eligible(child)
    assert any(s.get("reason") == "PENDING_CAPABLE_BUILDER" for s in second["skipped"])

    t1, t2 = parent["id"] + "-t1", parent["id"] + "-t2"
    items = [
        {"id": t, "repository": REPO, "status": "approved", "level": 1}
        for t in (t1, t2)
    ]
    third = refill_pass(
        world, items=items[:1], state={"items": {t1: {"outcome": "MERGED"}}}
    )
    assert [r["id"] for r in third["local_requests"]] == [t2]
    refill_pass(
        world,
        items=items,
        state={"items": {t1: {"outcome": "MERGED"}, t2: {"outcome": "MERGED"}}},
    )
    assert td.done_parent_ids(world["lane"]) == {parent["id"]}


def test_a_blocked_plan_leaves_the_slice_exactly_where_it_was(world: dict) -> None:
    (parent,) = refill_pass(world)["capable_requests"]
    td.block(world["lane"], parent, "RESIDUAL_NOT_LOCAL: needs a subprocess")
    again = refill_pass(world)
    assert [r["id"] for r in again["capable_requests"]] == [parent["id"]] and again[
        "local_requests"
    ] == []


# ---- staged proposal ----------------------------------------------------------------------------------------------------

DETAIL = {
    "slugify": {"signature": "def slugify(title: str) -> str:", "done_when": "lowercases and hyphenates",
                "acceptance_examples": GOOD["subtasks"][0]["acceptance_examples"]},
    "clamp": {"signature": "def clamp(value: int, low: int, high: int) -> int:", "done_when": "keeps a value in range",
              "acceptance_examples": GOOD["subtasks"][1]["acceptance_examples"]},
}
OUTLINE = {"functions": [{"name": "slugify", "purpose": "hyphenate a title", "criteria": [1], "depends_on": []},
                         {"name": "clamp", "purpose": "limit a number", "criteria": [2], "depends_on": ["slugify"],
                          "module_path": "docs/not_chosen_by_the_model.py"}], "residual": []}


def staged_ask(outline: Any = OUTLINE, detail: dict | None = None, prompts: list | None = None):
    def ask(prompt: str) -> Any:
        if prompts is not None:
            prompts.append(prompt)
        if prompt.startswith("Split this"):
            return outline
        name = prompt.split("named ", 1)[1].split(":", 1)[0]
        return (detail or DETAIL).get(name, {})
    return ask


def test_module_paths_are_assembled_from_the_allowed_paths_never_chosen_by_the_model() -> None:
    free = lambda _p: False  # noqa: E731
    assert td.module_for(PARENT, "slugify", set(), free) == "pkg/slugify.py"  # tests/** and docs are not code paths
    assert td.module_for(PARENT, "slugify", {"pkg/slugify.py"}, free) is None
    assert td.module_for(PARENT, "slugify", set(), lambda p: p == "pkg/slugify.py") is None
    glob_request = {**PARENT, "allowed_paths": ["knowledge_engine/online_*.py", "docs/**"], "forbidden_paths": []}
    assert td.module_for(glob_request, "bundle", set(), free) == "knowledge_engine/online_bundle.py"
    assert td.module_for({**PARENT, "allowed_paths": ["docs/**", "tests/**"]}, "x_y", set(), free) is None


def test_the_staged_proposal_asks_small_questions_and_assembles_a_plan_that_validates() -> None:
    prompts: list[str] = []
    result = td.propose_staged(PARENT, "existing python files: pkg/core.py", staged_ask(prompts=prompts), exists=lambda _p: False)
    assert result["ok"], result
    assert len(prompts) == 3 and all(len(p) < 2500 for p in prompts)  # one outline + one call per function, all small
    plan = result["plan"]
    assert [s["module_path"] for s in plan["subtasks"]] == ["pkg/slugify.py", "pkg/clamp.py"]  # model's path ignored
    assert plan["subtasks"][1]["depends_on"] == ["t1"] and plan["coverage"] == {"1": ["t1"], "2": ["t2"]}


def test_a_residual_the_model_reports_blocks_the_slice_instead_of_being_hidden() -> None:
    outline = {**OUTLINE, "residual": ["wire the helpers into the existing handler"]}
    result = td.propose_staged(PARENT, "", staged_ask(outline), exists=lambda _p: False)
    assert not result["ok"] and "RESIDUAL_NOT_LOCAL" in result["reason"]


def test_a_function_whose_examples_stay_unusable_is_rejected_after_one_retry_with_the_reason() -> None:
    prompts: list[str] = []
    broken = {**DETAIL, "clamp": {"signature": "def clamp(value: int) -> int:", "done_when": "x",
                                  "acceptance_examples": [{"call": "clamp(1)", "expected": 1, "why": "no placeholder"}]}}
    result = td.propose_staged(PARENT, "", staged_ask(detail=broken, prompts=prompts), exists=lambda _p: False)
    assert not result["ok"] and result["reason"].startswith("PLAN_REJECTED")
    assert sum("named clamp" in p for p in prompts) == td.MAX_FUNCTION_TRIES
    assert any("was rejected" in p for p in prompts if "named clamp" in p)


@pytest.mark.parametrize("outline", [{}, {"functions": []}, {"functions": ["x"]},
                                     {"functions": [{"name": "Bad Name"}], "residual": []},
                                     {"functions": [{"name": "same_name"}, {"name": "same_name"}], "residual": []}])
def test_an_unusable_outline_is_rejected_without_raising(outline: Any) -> None:
    result = td.propose_staged(PARENT, "", staged_ask(outline), exists=lambda _p: False)
    assert not result["ok"] and result["reason"].startswith("PLAN_REJECTED")


def test_a_dead_model_is_a_blocker_for_the_staged_proposal_too() -> None:
    def dead(_prompt: str) -> Any:
        raise TimeoutError("no answer")

    result = td.propose_staged(PARENT, "", dead, exists=lambda _p: False)
    assert not result["ok"] and result["reason"].startswith("MODEL_UNAVAILABLE")


# ---- review findings on #638 ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("residual", ["wire the helpers into the existing handler", None, {"a": 1}, 0])
def test_a_missing_or_mistyped_residual_is_never_read_as_nothing_left(residual: Any) -> None:
    outline = {"functions": OUTLINE["functions"]}
    if residual is not None:
        outline["residual"] = residual
    result = td.propose_staged(PARENT, "", staged_ask(outline), exists=lambda _p: False)
    assert not result["ok"] and "residual must be a list" in result["reason"]


def _draft_job(lane: Path, ident: str, attempt: int, stage: str, reason: str = "") -> None:
    path = lane / drafter.JOBS / f"{ident}.a{attempt}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": ident, "attempt": attempt, "stage": stage, "reason": reason,
                                "started_at": 0, "result": str(lane / "r.json")}), encoding="utf-8")


def test_a_subtask_whose_drafting_attempts_are_spent_blocks_the_plan_and_releases_the_parent(tmp_path: Path) -> None:
    lane = tmp_path / "lane"
    active_plan(lane)
    t1 = PARENT["id"] + "-t1"
    _draft_job(lane, t1, 0, "FAILED", "ContradictsRoadmap")
    assert td.settle(lane, [], {}) == [] and td.active_parent_ids(lane) == {PARENT["id"]}  # one attempt left
    _draft_job(lane, t1, 1, "FAILED", "PLAN_REJECTED")
    (change,) = td.settle(lane, [], {})
    assert change["status"] == "BLOCKED" and "SUBTASK_EXHAUSTED: t1" in change["blocker"]
    assert td.active_parent_ids(lane) == frozenset()  # the stronger builder may take the parent


def test_running_or_interrupted_drafting_does_not_block_the_plan(tmp_path: Path) -> None:
    lane = tmp_path / "lane"
    active_plan(lane)
    t1 = PARENT["id"] + "-t1"
    _draft_job(lane, t1, 0, "INTERRUPTED", "EXECUTION_AUTHORITY_REVOKED")
    _draft_job(lane, t1, 1, "FAILED", "x")
    _draft_job(lane, t1, 2, "DRAFTING")
    assert td.settle(lane, [], {}) == [] and td.active_parent_ids(lane) == {PARENT["id"]}
    assert td.child_requests(lane, REPO, "b" * 40, [], {})  # still offered while a draft is running


def test_a_reauthored_subtask_with_spent_drafting_also_releases_the_parent(tmp_path: Path) -> None:
    lane = tmp_path / "lane"
    active_plan(lane)
    t1 = PARENT["id"] + "-t1"
    failed = {"id": t1, "repository": REPO, "status": "approved", "level": 1}
    state = {"items": {t1: {"outcome": "FAIL", "attempts": 3}}}
    _draft_job(lane, t1 + "-r1", 0, "FAILED", "a")
    _draft_job(lane, t1 + "-r1", 1, "FAILED", "b")
    (change,) = td.settle(lane, [failed], state)
    assert change["status"] == "BLOCKED"
