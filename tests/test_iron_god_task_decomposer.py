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


def test_a_residual_the_model_reports_is_recorded_on_the_plan_never_hidden() -> None:
    """Owner direction 2026-11-08: a partial plan is useful work; what it leaves is recorded and returns to the stronger builder."""
    outline = {**OUTLINE, "residual": ["wire the helpers into the existing handler"]}
    result = td.propose_staged(PARENT, "", staged_ask(outline), exists=lambda _p: False)
    assert result["ok"] and result["plan"]["residual"] == ["wire the helpers into the existing handler"]
    assert [s["function_name"] for s in result["plan"]["subtasks"]] == ["slugify", "clamp"]


def test_a_function_whose_examples_stay_unusable_is_retried_then_dropped_and_its_criterion_left_for_others() -> None:
    prompts: list[str] = []
    broken = {**DETAIL, "clamp": {"signature": "def clamp(value: int) -> int:", "done_when": "x",
                                  "acceptance_examples": [{"call": "{fn}(undefined_name)", "expected": 1, "why": "not a literal"}]}}
    result = td.propose_staged(PARENT, "", staged_ask(detail=broken, prompts=prompts), exists=lambda _p: False)
    assert sum("named clamp" in p for p in prompts) == td.MAX_FUNCTION_TRIES             # retried once, with the reason
    assert any("was rejected" in p for p in prompts if "named clamp" in p)
    assert result["ok"]                                                                    # slugify is still useful work
    plan = result["plan"]
    assert [s["function_name"] for s in plan["subtasks"]] == ["slugify"] and plan["coverage"] == {"1": ["t1"]}
    assert plan["residual"] == [PARENT["slice_acceptance"][1]]                             # clamp's criterion is left, named
    assert plan["dropped"][0]["title"] and plan["dropped"][0]["problems"]


def test_the_models_name_slip_is_repaired_mechanically_and_nothing_else_is_changed() -> None:
    examples = [{"call": "clamp(1, 0, 3)", "expected": 1, "why": "in range"}, {"call": "{fn}(9, 0, 3)", "expected": 3, "why": "x"},
                {"call": "other(1)", "expected": 1, "why": "a different function stays wrong"}]
    fixed = td.normalize_examples("clamp", examples)
    assert [e["call"] for e in fixed] == ["{fn}(1, 0, 3)", "{fn}(9, 0, 3)", "other(1)"]
    assert [e["expected"] for e in fixed] == [1, 3, 1] and examples[0]["call"] == "clamp(1, 0, 3)"   # input untouched
    prompts: list[str] = []
    slipped = {**DETAIL, "clamp": {**DETAIL["clamp"], "acceptance_examples": [
        {**e, "call": e["call"].replace("{fn}", "clamp")} for e in DETAIL["clamp"]["acceptance_examples"]]}}
    result = td.propose_staged(PARENT, "", staged_ask(detail=slipped, prompts=prompts), exists=lambda _p: False)
    assert result["ok"] and not result["plan"]["residual"]                                  # the whole slice, no retry spent
    assert sum("named clamp" in p for p in prompts) == 1


def test_a_step_that_depends_on_a_dropped_step_is_dropped_too_and_nothing_valid_means_no_plan() -> None:
    broken_slugify = {**DETAIL, "slugify": {"signature": "def slugify(title: str) -> str:", "done_when": "x",
                                            "acceptance_examples": [{"call": "{fn}(t)", "expected": "a", "why": "no literal"}]}}
    result = td.propose_staged(PARENT, "", staged_ask(detail=broken_slugify), exists=lambda _p: False)
    assert not result["ok"] and result["reason"].startswith("PLAN_REJECTED")     # clamp depends on slugify: nothing is left


def test_a_partial_plan_is_never_reported_complete(tmp_path: Path) -> None:
    lane = tmp_path / "lane"
    result = td.propose_staged(PARENT, "", staged_ask({**OUTLINE, "residual": ["wire it into the handler"]}), exists=lambda _p: False)
    td.write_plan(lane, PARENT, result["plan"], planner="test")
    items = [{"id": f"{PARENT['id']}-t1"}, {"id": f"{PARENT['id']}-t2"}]
    state = {"items": {i["id"]: {"status": "done", "outcome": "MERGED"} for i in items}}
    original = td.refill._slice_status
    try:
        td.refill._slice_status = lambda base, _items, _state: ("DONE", base)
        changes = td.settle(lane, items, state)
    finally:
        td.refill._slice_status = original
    assert changes and changes[0]["status"] == "PARTIAL" and "wire it into the handler" in changes[0]["blocker"]
    assert td.done_parent_ids(lane) == frozenset() and td.active_parent_ids(lane) == frozenset()   # not done, not held


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


# ---- Level 2 edit subtasks ------------------------------------------------------------------------------------------------

CORE = "def one():\n    return 1\n\n\ndef clean(text):\n    return text.strip()\n"
FILES = {"pkg/core.py": CORE}
TEST = "from pkg.core import one\n\n\ndef test_one_is_two():\n    assert one() == 2\n"


def file_exists(path: str) -> bool:
    return path in FILES


def file_read(path: str) -> str | None:
    return FILES.get(path)


EDIT = {"key": "t1", "kind": "edit", "title": "make one return two", "depends_on": [], "module_path": "pkg/core.py",
        "targets": ["one"], "test_path": "tests/test_core_one.py", "test_source": TEST,
        "done_when": "one() returns 2 instead of 1 so callers get the new constant"}
EDIT_PLAN = {"subtasks": [EDIT, {**GOOD["subtasks"][0], "key": "t2", "depends_on": ["t1"]}],
             "coverage": {"1": ["t2"], "2": ["t1"]}, "residual": []}


def edit_check(raw: Any, **kw):
    return td.validate_plan(raw, PARENT, exists=kw.get("exists", file_exists), read=kw.get("read", file_read))


def test_a_level_two_edit_subtask_is_accepted_when_the_units_exist_and_the_test_is_well_formed() -> None:
    plan, problems = edit_check(EDIT_PLAN)
    assert problems == [] and plan is not None
    assert plan["subtasks"][0]["kind"] == "edit" and plan["subtasks"][0]["targets"] == ["one"]
    assert plan["subtasks"][1]["function_name"] == "slugify"  # a Level 1 step can sit beside it


def edit_variant(**change: Any) -> dict:
    return {**EDIT_PLAN, "subtasks": [{**EDIT, **change}, EDIT_PLAN["subtasks"][1]]}


@pytest.mark.parametrize("change, fragment", [
    ({"targets": ["missing_unit"]}, "do not exist in pkg/core.py"),
    ({"targets": ["one", "clean", "one", "clean"]}, "targets must name"),
    ({"module_path": "pkg/other.py"}, "does not exist at the base"),
    ({"module_path": "docs/core.py"}, "outside the slice's allowed paths"),
    ({"module_path": "tests/test_core.py"}, "outside the slice's allowed paths, a test file"),
    ({"test_path": "pkg/test_core_one.py"}, "new tests/**/test_*.py"),
    ({"test_path": "tests/core_one.py"}, "new tests/**/test_*.py"),
    ({"test_source": "def test_x():\n    pass\n"}, "must parse, define test_"),
    ({"test_source": "from pkg.core import one\n\ndef helper():\n    assert one()\n"}, "must parse, define test_"),
    ({"test_source": "def test_x(:\n"}, "must parse, define test_"),
    ({"test_source": "def test_x():\n    assert 1 == 1\n"}, "must parse, define test_"),
    ({"done_when": "short"}, "20+ characters"),
])
def test_an_edit_subtask_that_breaks_a_rule_is_rejected(change: dict, fragment: str) -> None:
    plan, problems = edit_check(edit_variant(**change))
    assert plan is None and any(fragment in p for p in problems), problems


def test_an_edit_needs_repository_access_and_a_new_test_file() -> None:
    plan, problems = edit_check(EDIT_PLAN, read=None)
    assert plan is None and any("repository access" in p for p in problems)
    plan, problems = edit_check(EDIT_PLAN, exists=lambda p: p in {*FILES, "tests/test_core_one.py"})
    assert plan is None and any("already exists" in p for p in problems)


def test_two_edits_of_one_module_must_be_ordered() -> None:
    second = {**EDIT, "key": "t2", "targets": ["clean"], "test_path": "tests/test_core_clean.py",
              "test_source": "from pkg.core import clean\n\n\ndef test_clean():\n    assert clean(' a ') == 'a'\n"}
    raw = {"subtasks": [EDIT, second], "coverage": {"1": ["t1"], "2": ["t2"]}, "residual": []}
    plan, problems = edit_check(raw)
    assert plan is None and any("must depend on t1" in p for p in problems)
    second["depends_on"] = ["t1"]
    plan, problems = edit_check(raw)
    assert problems == [] and plan is not None


def test_an_edit_subtask_becomes_a_level_two_directed_request_the_unchanged_drafter_takes(tmp_path: Path) -> None:
    lane = tmp_path / "lane"
    plan, _ = edit_check(EDIT_PLAN)
    td.write_plan(lane, PARENT, plan, planner="qwen2.5-coder:7b")
    (first,) = td.child_requests(lane, REPO, "b" * 40, [], {})
    assert first["id"] == PARENT["id"] + "-t1" and first["capability_level"] == 2
    assert first["directed_edit"]["targets"] == ["one"] and first["directed_edit"]["test_path"] == "tests/test_core_one.py"
    assert first["allowed_paths"] == ["pkg/core.py", "tests/test_core_one.py"] and first["acceptance_examples"] == []
    first["route"] = {"builder": refill.LOCAL}
    assert drafter.eligible(first) and drafter.directed_spec(first) is not None


def test_the_test_path_is_assembled_by_code_from_the_allowed_test_paths() -> None:
    free = lambda _p: False  # noqa: E731
    assert td.test_for(PARENT, "core_one", set(), free) == "tests/test_core_one.py"
    assert td.test_for(PARENT, "core_one", {"tests/test_core_one.py"}, free) is None
    assert td.test_for(PARENT, "core_one", set(), lambda p: p == "tests/test_core_one.py") is None
    assert td.test_for({**PARENT, "allowed_paths": ["pkg/**"]}, "x", set(), free) is None
    assert td.import_hint("src/project_orchestrator/x.py") == "project_orchestrator.x"
    assert td.import_hint("pkg/core.py") == "pkg.core"


def edit_ask(prompts: list | None = None, test_source: str = TEST):
    def ask(prompt: str) -> Any:
        if prompts is not None:
            prompts.append(prompt)
        if prompt.startswith("Split this") or prompt.startswith("Split"):
            return {"functions": [
                {"name": "bump_one", "kind": "edit", "module": "pkg/core.py", "targets": ["one"],
                 "purpose": "one() returns 2 instead of 1 so callers get the new constant", "criteria": [2],
                 "depends_on": [], "test_path": "tests/not_chosen_by_the_model.py"},
                {"name": "slugify", "kind": "new", "purpose": "hyphenate a title", "criteria": [1],
                 "depends_on": ["bump_one"]}], "residual": []}
        if "pytest test file" in prompt:
            return {"test_source": test_source, "done_when": "one() returns 2 instead of 1 for every caller"}
        return DETAIL["slugify"]
    return ask


def test_the_staged_proposal_writes_the_test_for_an_edit_and_assembles_the_test_path() -> None:
    prompts: list[str] = []
    result = td.propose_staged(PARENT, "", edit_ask(prompts), exists=file_exists, read=file_read)
    assert result["ok"], result
    edit = result["plan"]["subtasks"][0]
    assert edit["kind"] == "edit" and edit["test_path"] == "tests/test_core_one.py" and edit["targets"] == ["one"]
    shown = next(p for p in prompts if "pytest test file" in p)
    assert "def one():" in shown and "from pkg.core import" in shown and "FAILS on the current code" in shown
    assert result["plan"]["subtasks"][1]["depends_on"] == ["t1"]


def test_an_edit_whose_test_stays_unusable_is_rejected_after_one_retry() -> None:
    prompts: list[str] = []
    result = td.propose_staged(PARENT, "", edit_ask(prompts, test_source="def test_x():\n    pass\n"),
                               exists=file_exists, read=file_read)
    assert not result["ok"] and result["reason"].startswith("PLAN_REJECTED")
    assert sum("pytest test file" in p for p in prompts) == td.MAX_FUNCTION_TRIES


def test_an_edit_of_a_module_that_is_not_there_is_rejected_not_invented() -> None:
    result = td.propose_staged(PARENT, "", edit_ask(), exists=lambda p: False, read=lambda p: None)
    assert not result["ok"] and "does not exist at the base" in result["reason"]


def test_a_finished_planning_job_with_an_edit_is_revalidated_against_the_real_repository(tmp_path: Path, clone: Path) -> None:
    lane, base = tmp_path / "lane", head_of(clone)
    raw = {"subtasks": [EDIT, {**GOOD["subtasks"][0], "key": "t2", "depends_on": ["t1"]}],
           "coverage": {"1": ["t2"], "2": ["t1"]}, "residual": []}
    td.advance(lane, [PARENT], clone, base, spawn=Spawner({"ok": True, "raw": raw}))
    assert td.harvest(lane)[0] == {"id": PARENT["id"], "status": "PLANNED", "subtasks": 2}
    bad = {**raw, "subtasks": [{**EDIT, "targets": ["absent"]}, raw["subtasks"][1]]}
    other = {**PARENT, "id": PARENT["id"] + "y"}
    td.advance(lane, [other], clone, base, spawn=Spawner({"ok": True, "raw": bad}))
    assert td.harvest(lane)[0]["reason"] == "PLAN_REJECTED"  # the real clone is the authority, not the model's claim


def test_a_def_line_missing_only_its_colon_is_repaired_and_nothing_else_is() -> None:
    assert td.normalize_signature("def f(x: str) -> str") == "def f(x: str) -> str:"
    assert td.normalize_signature("  def  f(x)  ->  int:  ") == "def f(x) -> int:"
    assert td.normalize_signature(None) == "" and td.normalize_signature("not python") == "not python:"
    detail = {**DETAIL, "slugify": {**DETAIL["slugify"], "signature": "def slugify(title: str) -> str"}}
    result = td.propose_staged(PARENT, "", staged_ask(detail=detail), exists=lambda _p: False)
    assert result["ok"] and result["plan"]["subtasks"][0]["signature"] == "def slugify(title: str) -> str:"


def test_a_directed_edit_subtask_is_exhausted_after_its_single_attempt(tmp_path: Path) -> None:
    lane = tmp_path / "lane"
    plan, _ = edit_check(EDIT_PLAN)
    td.write_plan(lane, PARENT, plan, planner="qwen2.5-coder:7b")
    t1, t2 = PARENT["id"] + "-t1", PARENT["id"] + "-t2"
    _draft_job(lane, t2, 0, "FAILED", "x")
    assert td.settle(lane, [], {}) == []  # a Level 1 step still has a second attempt
    _draft_job(lane, t1, 0, "FAILED", "TestPassesAtBase")  # the drafter never retries a directed edit
    (change,) = td.settle(lane, [], {})
    assert change["status"] == "BLOCKED" and "SUBTASK_EXHAUSTED: t1" in change["blocker"]
    assert td.active_parent_ids(lane) == frozenset()
    assert td.drafting_exhausted(lane, t1, directed=True) and not td.drafting_exhausted(lane, t1)


# ---- a slice with nowhere for a local subtask is released at once, and structural rejections are never retried -------------


def scoped(*allowed: str, forbidden: tuple[str, ...] = ()) -> dict:
    return {**PARENT, "allowed_paths": list(allowed), "forbidden_paths": list(forbidden)}


@pytest.mark.parametrize("allowed, forbidden, missing", [
    (("docs/**", "tests/**"), (), True),                    # a verify-only slice
    (("Source/**", "Config/**"), (), True),                 # no Python anywhere in scope
    (("pkg/**", "tests/**"), (), False),                    # a new module can be placed (and pkg/core.py can be edited)
    (("pkg/core.py",), (), False),                          # an existing module can be edited
    (("pkg/core.py",), ("pkg/core.py",), True),             # ...unless it is forbidden
    (("tests/**", "pkg/test_core.py"), (), True),           # tests are never a local target
])
def test_a_slice_with_no_place_for_a_local_subtask_is_detected_from_the_repository(clone: Path, allowed, forbidden, missing) -> None:
    reason = td.local_target_missing(scoped(*allowed, forbidden=forbidden), clone, head_of(clone))
    assert (reason is not None and reason.startswith("NO_LOCAL_TARGET_IN_SCOPE")) is missing, reason


def test_such_a_slice_is_blocked_without_a_model_call_and_released_to_the_stronger_builder(tmp_path: Path, clone: Path) -> None:
    lane, spawner = tmp_path / "lane", Spawner()
    request = {**scoped("docs/**", "tests/**"), "id": refill.item_id(REPO, "cap-a", "verify")}
    outcomes = td.advance(lane, [request], clone, head_of(clone), spawn=spawner)
    assert spawner.launched == []                                   # no planning job, no model time
    assert outcomes == [{"id": request["id"], "status": "BLOCKED", "reason": outcomes[0]["reason"]}]
    plan = json.loads(td.plan_path(lane, request["id"]).read_text(encoding="utf-8"))
    assert plan["status"] == "BLOCKED" and plan["blocker"].startswith("NO_LOCAL_TARGET_IN_SCOPE")
    assert td.active_parent_ids(lane) == frozenset()                # the stronger builder may take it now


def test_a_structural_rejection_is_terminal_but_an_ordinary_one_still_gets_its_second_attempt(tmp_path: Path, clone: Path) -> None:
    base = head_of(clone)
    structural, spawner = tmp_path / "structural", Spawner({"ok": False, "reason": "PLAN_REJECTED: NO_LOCAL_TARGET_IN_SCOPE: no free allowed path for step"})
    td.advance(structural, [PARENT], clone, base, spawn=spawner)
    outcomes = td.advance(structural, [PARENT], clone, base, spawn=spawner)
    assert len(spawner.launched) == 1                               # never retried
    assert [o["status"] for o in outcomes] == ["FAILED", "BLOCKED"] and "NO_LOCAL_TARGET_IN_SCOPE" in outcomes[1]["reason"]
    assert json.loads(td.plan_path(structural, PARENT["id"]).read_text(encoding="utf-8"))["blocker"].startswith(
        "DECOMPOSITION_NOT_POSSIBLE")
    ordinary, spawner = tmp_path / "ordinary", Spawner({"ok": False, "reason": "PLAN_REJECTED: t1: signature does not parse"})
    td.advance(ordinary, [PARENT], clone, base, spawn=spawner)
    td.advance(ordinary, [PARENT], clone, base, spawn=spawner)
    assert len(spawner.launched) == 2                               # a model slip still gets another sample


def test_a_later_allowed_placement_is_enough_even_when_the_first_pattern_has_no_python(clone: Path) -> None:
    base = head_of(clone)
    reordered = scoped("empty/**", "pkg/new_*.py", "tests/**")          # empty/ first, a valid placement second
    assert td.local_target_missing(reordered, clone, base) is None
    assert td.local_target_missing(scoped("pkg/new_*.py", "empty/**", "tests/**"), clone, base) is None
    assert td.local_target_missing(scoped("empty/**", "tests/**"), clone, base) is not None
    assert list(td.module_candidates(reordered, "x", set(), lambda _p: False))[:2] == ["empty/x.py", "pkg/new_x.py"]


def test_a_name_collision_is_an_ordinary_slip_and_only_a_scope_without_a_target_is_terminal(tmp_path: Path, clone: Path) -> None:
    base = head_of(clone)
    outline = {"functions": [{"name": "core", "kind": "new", "purpose": "collides with pkg/core.py", "criteria": [1],
                              "depends_on": []}], "residual": []}
    collision = td.propose_staged(PARENT, "", lambda _prompt: outline, exists=td._exists_at(clone, base))
    assert not collision["ok"] and "already exists in scope" in collision["reason"]
    assert not any(m in collision["reason"] for m in td.TERMINAL_MARKERS)
    no_room = {**PARENT, "allowed_paths": ["docs/**", "tests/**"]}
    structural = td.propose_staged(no_room, "", lambda _prompt: outline, exists=td._exists_at(clone, base))
    assert not structural["ok"] and "NO_LOCAL_TARGET_IN_SCOPE" in structural["reason"]
    spawner = Spawner({"ok": False, "reason": collision["reason"]})
    td.advance(tmp_path / "lane", [PARENT], clone, base, spawn=spawner)
    td.advance(tmp_path / "lane", [PARENT], clone, base, spawn=spawner)
    assert len(spawner.launched) == 2                       # the collision got its second attempt


def test_waiting_for_memory_spends_no_attempt_and_returns_the_lease(tmp_path: Path, clone: Path) -> None:
    lane, base, released = tmp_path / "lane", head_of(clone), []
    waiting = {"ok": False, "reason": "MODEL_UNAVAILABLE: ModelNotAdmitted: MEMORY: 1.0 GiB available < 3.8 GiB required"}
    for _ in range(td.MAX_PLAN_ATTEMPTS + 3):  # far more waits than attempts
        td.advance(lane, [PARENT], clone, base, spawn=Spawner(waiting), reserve=lambda: "lease-1", release=released.append)
        outcomes = td.harvest(lane, release=released.append)
        assert outcomes and outcomes[0]["status"] == "WAITING_FOR_MEMORY"
    assert released and not td.plan_path(lane, PARENT["id"]).exists()
    assert not [r for r in td._jobs(lane) if r.get("stage") == "FAILED"]
    assert not any(o["status"] == "BLOCKED" for o in td.advance(lane, [PARENT], clone, base, spawn=Spawner(waiting)))



def test_a_partial_plan_survives_the_real_harvest_and_becomes_an_active_plan_with_its_residual(tmp_path: Path, clone: Path) -> None:
    """Review P1 on #659: harvest re-validated with the strict validator, so a partial plan the proposer accepted was
    rejected as RESIDUAL_NOT_LOCAL in the real worker lifecycle and never written."""
    lane, base = tmp_path / "lane", head_of(clone)
    partial = {**GOOD, "residual": ["wire the helpers into the existing handler"]}
    td.advance(lane, [PARENT], clone, base, spawn=Spawner({"ok": True, "raw": partial, "model": "qwen2.5-coder:7b"}))
    (outcome,) = td.harvest(lane)
    assert outcome["status"] == "PLANNED", outcome
    plan = json.loads(td.plan_path(lane, PARENT["id"]).read_text(encoding="utf-8"))
    assert plan["status"] == "ACTIVE" and plan["residual"] == ["wire the helpers into the existing handler"]


def test_a_criterion_that_loses_one_of_its_owners_keeps_its_unfinished_work_in_the_residual() -> None:
    """Review P2 on #659: one criterion needing both steps, the clamp step dropped: the slugify step alone must not make the
    criterion look complete, or settle() would mark an incomplete slice DONE."""
    joint = {**PARENT, "slice_acceptance": ["slugify a title and clamp its length", PARENT["slice_acceptance"][2]]}
    outline = {"functions": [{"name": "slugify", "purpose": "hyphenate a title", "criteria": [1], "depends_on": []},
                             {"name": "clamp", "purpose": "limit a number", "criteria": [1], "depends_on": []}], "residual": []}
    broken = {**DETAIL, "clamp": {"signature": "def clamp(value: int) -> int:", "done_when": "x",
                                  "acceptance_examples": [{"call": "{fn}(undefined_name)", "expected": 1, "why": "not a literal"}]}}
    result = td.propose_staged(joint, "", staged_ask(outline, broken), exists=lambda _p: False)
    assert result["ok"] and [s["function_name"] for s in result["plan"]["subtasks"]] == ["slugify"]
    assert result["plan"]["residual"] == ["slugify a title and clamp its length"]       # not empty: the clamp part is missing



def test_a_native_cpp_slice_is_never_decomposed_into_python_steps() -> None:
    """Found live 2026-11-08: Everward's C++ first-loop test slice came back as invented Python functions, and the module
    placer would have written .py files under the C++ src/simulation tree."""
    everward = {**PARENT, "repository": "jweter/Project-Everward",
                "allowed_paths": ["src/simulation/**", "tests/**", "Tests/**", "docs/**", "tools/**"]}
    assert td.native_scope(everward) and not td.decomposable(everward)
    assert td.native_scope({**everward, "allowed_paths": ["**"]})                          # everything includes native code
    assert not td.native_scope({**everward, "allowed_paths": ["tools/**", "docs/**"]})     # Python tooling only: allowed
    assert td.decomposable({**everward, "allowed_paths": ["tools/**", "tests/**"]})
    assert not td.native_scope(PARENT) and td.decomposable(PARENT)                         # a Python repository is unchanged


def test_model_authored_nested_placeholder_oracle_is_rejected_before_child_admission() -> None:
    plan = mutated(lambda p: p["subtasks"][0]["acceptance_examples"][0].update(
        expected=json.dumps({"sections": [{"title": "Results", "content": "..."}]})
    ))
    built, problems = check(plan)
    assert built is None
    assert any("t1: SELF_AUTHORED_ACCEPTANCE_PLACEHOLDER" in problem for problem in problems)
    accepted, clean = check(GOOD)
    assert clean == [] and accepted is not None


@pytest.mark.parametrize("expected", [
    '{"status":"Server started on port <ephemeral port>"}',  # the recorded oracle (attempts 1-3)
    '{"status":"Server started on port <ephemeral_port>"}',  # the recorded oracle (attempt 4)
    "Server started on port <port>",
    ["ok", "<request-id>"],
])
def test_a_model_authored_oracle_with_an_embedded_slot_is_rejected(expected) -> None:
    """knowledge-engine-core 2026-11-14: four local builds were spent on an output no implementation can return."""
    plan = mutated(lambda p: p["subtasks"][0]["acceptance_examples"][0].update(expected=expected))
    built, problems = check(plan)
    assert built is None
    assert any("t1: SELF_AUTHORED_ACCEPTANCE_PLACEHOLDER" in problem for problem in problems)


@pytest.mark.parametrize("expected", [
    "<b>bold</b>", '<a href="x">link</a>', "<br/>", "a < b and c > d", "List[int] -> <class 'int'>", "<p>one</p><p>two</p>",
])
def test_markup_and_comparisons_in_an_oracle_are_not_slots(expected) -> None:
    plan = mutated(lambda p: p["subtasks"][0]["acceptance_examples"][0].update(expected=expected))
    accepted, problems = check(plan)
    assert accepted is not None and problems == []


def test_decomposition_with_literal_ellipsis_punctuation_is_not_rejected_as_placeholder() -> None:
    plan = mutated(lambda p: p["subtasks"][0]["acceptance_examples"][0].update(
        expected="First... then done"
    ))
    accepted, problems = check(plan)
    assert accepted is not None and problems == []


# ---- grounding the model's edit targets in the module's real units -----------------------------------------------------

def test_a_misspelled_target_is_snapped_to_the_one_real_unit_it_nearly_matches() -> None:
    entries = [{"name": "bump", "kind": "edit", "module": "pkg/core.py", "targets": ["on"]}]
    fixed, problems = td.ground_edit_targets(entries, lambda _m: {"one", "other_thing"})
    assert fixed[0]["targets"] == ["one"] and problems == []


def test_a_target_that_does_not_exist_is_reported_with_the_real_unit_names() -> None:
    entries = [{"name": "bump", "kind": "edit", "module": "pkg/core.py", "targets": ["align_notes"]},
               {"name": "slugify", "kind": "new"}]
    fixed, problems = td.ground_edit_targets(entries, lambda _m: {"align_onsets_to_beats", "refine_tempo"})
    assert fixed[0]["targets"] == ["align_notes"] and fixed[1] == entries[1]
    assert len(problems) == 1 and "align_notes" in problems[0] and "align_onsets_to_beats" in problems[0]


def test_unreadable_or_non_edit_entries_are_left_for_the_existing_validation() -> None:
    entries = [{"name": "bump", "kind": "edit", "module": "pkg/missing.py", "targets": ["whatever"]}]
    assert td.ground_edit_targets(entries, lambda _m: None) == (entries, [])


def test_a_made_up_unit_name_gets_one_more_outline_with_the_real_names_and_then_passes() -> None:
    prompts: list[str] = []
    real = edit_ask(prompts)

    def ask(prompt: str) -> Any:
        if prompt.startswith("Split") and "do not exist" not in prompt:
            first = real(prompt)
            first["functions"][0]["targets"] = ["align_notes"]  # the slip: a plausible unit that is not in the module
            return first
        return real(prompt)

    result = td.propose_staged(PARENT, "", ask, exists=file_exists, read=file_read)
    assert result["ok"], result
    retry = [p for p in prompts if p.startswith("Split") and "do not exist" in p]
    assert len(retry) == 1 and "align_notes" in retry[0] and "'one'" in retry[0]
    assert result["plan"]["subtasks"][0]["targets"] == ["one"]
    assert any(step.get("grounding_retry") for step in result["trail"])


def test_a_made_up_unit_name_that_survives_the_retry_is_still_rejected() -> None:
    real = edit_ask()

    def ask(prompt: str) -> Any:
        out = real(prompt)
        if prompt.startswith("Split"):
            out["functions"][0]["targets"] = ["align_notes"]
        return out

    result = td.propose_staged(PARENT, "", ask, exists=file_exists, read=file_read)
    assert not result["ok"] and "align_notes" in result["reason"]


def test_a_snapped_target_is_recorded_original_to_real_and_kept_in_the_plan_trail() -> None:
    entries = [{"name": "bump", "kind": "edit", "module": "pkg/core.py", "targets": ["on"], "purpose": "make it return two"}]
    fixed, problems = td.ground_edit_targets(entries, lambda _m: {"one", "other_thing"})
    assert problems == [] and fixed[0]["targets"] == ["one"]
    assert fixed[0]["snapped"] == [{"module": "pkg/core.py", "from": "on", "to": "one", "similarity": 0.8}]


def test_a_close_name_is_not_snapped_when_the_purpose_points_at_a_different_real_unit() -> None:
    """One close match is not proof of intent: if the model's own purpose names another real function, ask again instead."""
    entries = [{"name": "bump", "kind": "edit", "module": "pkg/core.py", "targets": ["align_notes"],
                "purpose": "fix refine_tempo so it handles an empty list"}]
    fixed, problems = td.ground_edit_targets(entries, lambda _m: {"align_note", "refine_tempo"})
    assert fixed[0]["targets"] == ["align_notes"] and "snapped" not in fixed[0]
    assert len(problems) == 1 and "align_notes" in problems[0] and "refine_tempo" in problems[0]


def test_the_plan_trail_shows_every_snap_made_by_code() -> None:
    real = edit_ask()

    def ask(prompt: str) -> Any:
        out = real(prompt)
        if prompt.startswith("Split"):
            out["functions"][0]["targets"] = ["on"]
        return out

    result = td.propose_staged(PARENT, "", ask, exists=file_exists, read=file_read)
    assert result["ok"], result
    snaps = [step for step in result["trail"] if "snapped_targets" in step]
    assert snaps and snaps[0]["snapped_targets"][0]["from"] == "on" and snaps[0]["snapped_targets"][0]["to"] == "one"


# ---- a "pure function" that needs a file cannot be specified by literal examples ---------------------------------------

def _io_sub(signature: str, calls: list[tuple[str, object]]) -> dict:
    return {"key": "t1", "signature": signature,
            "acceptance_examples": [{"call": call, "expected": expected, "why": "documented behaviour"} for call, expected in calls]}


def test_a_function_whose_examples_name_a_media_file_is_rejected_before_any_worker_attempt() -> None:
    """The 7B planned analyze_guide_vocal('path/to/guide_vocal.wav') with exact F0 values; the file never exists."""
    sub = _io_sub("def analyze_guide_vocal(path: str) -> dict:",
                  [("{fn}('path/to/guide_vocal.wav')", {"f0": [220.0]}), ("{fn}('a/b/take1.mp3')", {"f0": [247.0]}),
                   ("{fn}('x.flac')", {"f0": []})])
    problems = td._example_problems(sub)
    assert any("names a file that does not exist" in p and "pass the data itself" in p for p in problems)


def test_a_path_or_stream_annotation_marks_the_function_as_not_pure() -> None:
    sub = _io_sub("def load_notes(source: Path) -> list:",
                  [("{fn}(1)", [1]), ("{fn}(2)", [2]), ("{fn}(3)", [3])])
    assert any("takes a file or stream" in p for p in td._example_problems(sub))


def test_a_pure_string_function_that_merely_mentions_a_source_file_name_is_not_rejected() -> None:
    sub = _io_sub("def module_name(path: str) -> str:",
                  [("{fn}('pkg/mod.py')", "pkg.mod"), ("{fn}('pkg/sub/other.py')", "pkg.sub.other"),
                   ("{fn}('top.py')", "top")])
    problems = td._example_problems(sub)
    assert not any("file" in p and "pure function" in p for p in problems), problems


def test_a_pure_filename_parser_with_a_media_name_is_not_rejected() -> None:
    """Review (Codex P2, ChatGPT seq 232/236): a quoted media filename alone is not file access."""
    sub = _io_sub("def extension(filename: str) -> str:",
                  [("{fn}('song.wav')", "wav"), ("{fn}('take1.final.mp3')", "mp3"), ("{fn}('cover.png')", "png")])
    assert not any("pure function" in p for p in td._example_problems(sub))


def test_a_path_named_parameter_with_a_scalar_answer_is_a_name_check_not_a_read() -> None:
    sub = _io_sub("def is_audio_name(path: str) -> bool:",
                  [("{fn}('song.wav')", True), ("{fn}('notes.txt')", False), ("{fn}('mix.flac')", True)])
    assert not any("pure function" in p for p in td._example_problems(sub))


def test_a_path_parameter_with_a_media_file_and_structured_data_expected_is_a_read_and_is_rejected() -> None:
    sub = _io_sub("def read_samples(path: str) -> list:",
                  [("{fn}('take1.wav')", [0.1, 0.2]), ("{fn}('take2.wav')", [0.3]), ("{fn}('take3.wav')", [])])
    assert any("names a file that does not exist" in p for p in td._example_problems(sub))


def test_the_real_failed_voicestudio_plan_is_still_rejected_after_narrowing() -> None:
    expected = "{'f0_track': [220.0, 247.0, 262.0, 294.0, 330.0], 'voiced_segments': [[0.0, 1.0], [2.0, 3.0], [4.0, 5.0]]}"
    sub = _io_sub("def analyze_guide_vocal(audio_file_path: str) -> dict:",
                  [("{fn}('path/to/guide_vocal.wav')", expected), ("{fn}('path/to/another_guide_vocal.wav')", expected),
                   ("{fn}('path/to/third.wav')", expected)])
    assert any("names a file that does not exist" in p for p in td._example_problems(sub))


def test_a_function_whose_every_example_expects_none_is_an_effect_not_a_pure_function() -> None:
    """Measured 2026-11-13 (knowledge-engine-ai): copy_vendored_fixture(source_path, target_path) -> None; its tests failed."""
    for nothing in (None, "None"):
        sub = _io_sub("def copy_vendored_fixture(source_path: str, target_path: str) -> None:",
                      [("{fn}('core/tests/fixtures/', 'ai/tests/fixtures/')", nothing), ("{fn}('a/', 'b/')", nothing),
                       ("{fn}('c/', 'd/')", nothing)])
        assert any("specified only by a side effect" in p for p in td._example_problems(sub)), nothing


def test_a_function_that_returns_none_for_only_some_examples_is_still_a_pure_function() -> None:
    sub = _io_sub("def first_even(values: list) -> int | None:",
                  [("{fn}([1, 2, 3])", 2), ("{fn}([1, 3])", None), ("{fn}([])", None)])
    assert not any("side effect" in p for p in td._example_problems(sub))
