"""Roadmap-directed refill of the strict backlog (issue #594): compiler output becomes a valid backlog item."""

from __future__ import annotations

import copy
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_roadmap_refill as refill

PORTFOLIO = [
    "jweter/knowledge-engine-core",
    "jweter/knowledge-engine-web",
    "jweter/knowledge-engine-ai",
    "jweter/rocksmith-cdlc-generator",
    "jweter/Project-Everward",
    "jweter/VoiceStudio",
]
SOURCE = "engineering/capability-graph.json"
HEAD = "a" * 40


def graph(repository: str, *, state: str = "PLANNED", slices: object = "default", depends_on: tuple[str, ...] = (),
          extra: list[dict] | None = None) -> dict:
    slice_rows = [{"id": "s1", "state": "PLANNED", "objective": "Bound the cache by entry count.",
                   "acceptance": ["pruning keeps the newest entries"],
                   "allowed_paths": ["pkg/**", "tests/**"], "forbidden_paths": ["pkg/secret/**"]}]
    cap = {"id": "cap-cache", "capability": "Bounded cache", "state": state, "depends_on": list(depends_on),
           "completion_evidence": ["docs/ROADMAP.md cache section"],
           "slices": slice_rows if slices == "default" else slices}
    caps = [cap, *(extra or [])]
    return {"schema_version": 1, "repository": repository, "capabilities": caps, "roadmap": [c["id"] for c in caps]}


def run_git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def product(tmp_path: Path, repository: str, payload: dict | None, *, declare: bool = True) -> tuple[Path, str]:
    clone = tmp_path / "clones" / repository.split("/")[1]
    (clone / "pkg").mkdir(parents=True)
    (clone / "pkg" / "core.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (clone / "engineering").mkdir()
    if payload is not None:
        (clone / SOURCE).write_text(json.dumps(payload), encoding="utf-8")
    if declare:
        plane = {"schema_version": 1, "capability_graph": SOURCE, "authoritative_documents": [SOURCE]}
        (clone / "engineering" / "control-plane.json").write_text(json.dumps(plane), encoding="utf-8")
    run_git(tmp_path, "init", "-q", "-b", "main", str(clone))
    run_git(clone, "config", "user.email", "o@example.com")
    run_git(clone, "config", "user.name", "Owner")
    run_git(clone, "add", ".")
    run_git(clone, "commit", "-q", "-m", "product")
    return clone, run_git(clone, "rev-parse", "HEAD")


def request_for(repository: str = PORTFOLIO[0], **kw) -> dict:
    derived = refill.derive(repository, HEAD, graph(repository, **kw), SOURCE, items=[], state={})
    assert len(derived["requests"]) == 1, derived
    return derived["requests"][0]


def good_draft(request: dict, *, base: str = HEAD, **over) -> dict:
    draft = {
        "id": request["id"], "repository": request["repository"], "title": "Bound the cache",
        "roadmap_ref": request["roadmap_ref"], "addresses": f"{request['capability']}: entry-count bound",
        "why": "an unbounded cache grows without limit", "consumer": "the retrieval loop calls prune() after each put",
        "objective": "Make BoundedCache.prune keep only the newest max_entries entries.",
        "interfaces": ["pkg/core.py"], "depends_on": [], "acceptance_criteria": ["prune keeps the newest entries"],
        "tests": ["tests/test_core_prune.py"], "level": 2, "complexity": "low", "verification": "preflight",
        "definition_of_done": "merged with green CI", "approved_base": base,
        "direct": {"module_path": "pkg/core.py", "targets": ["one"], "direction": "return the newest entries",
                   "test_path": "tests/test_core_prune.py", "test_source": "def test_x():\n    assert False\n"}}
    draft.update(over)
    return draft


def no_changes(_base: str, _head: str) -> set[str]:
    return set()


def problems(draft, request, *, items=(), reserved=frozenset(), changed=no_changes, max_level=2, head=HEAD):
    return refill.draft_problems(draft, request, items=list(items), reserved=set(reserved), head=head,
                                 changed_since=changed, max_level=max_level)


# --- compiler bridge -----------------------------------------------------------------------------------------


def test_a_ready_capability_becomes_a_traceable_request_with_baseline_and_scope() -> None:
    request = request_for()

    assert request["id"] == refill.item_id(PORTFOLIO[0], "cap-cache", "s1") and request["exact_head"] == HEAD
    assert request["id"].startswith("RM-knowledge-engine-core-cap-cache-s1-")
    assert request["roadmap_ref"] == f"{SOURCE}#cap-cache/s1" and request["capability"] == "cap-cache"
    assert request["allowed_paths"] == ["pkg/**", "tests/**"] and request["forbidden_paths"] == ["pkg/secret/**"]
    assert request["slice_acceptance"] == ["pruning keeps the newest entries"]
    assert request["max_builder_level"] == 2 and request["prior_failures"] == []


@pytest.mark.parametrize("state", ["BLOCKED", "IMPLEMENTED_UNVERIFIED"])
def test_blocked_or_unverified_capabilities_never_produce_work_and_ask_for_direction(state: str) -> None:
    derived = refill.derive(PORTFOLIO[0], HEAD, graph(PORTFOLIO[0], state=state), SOURCE, items=[], state={})

    assert derived["requests"] == [] and derived["direction_required"][0]["question"]


def test_a_capability_with_unmet_dependencies_is_not_eligible() -> None:
    other = {"id": "cap-first", "capability": "First", "state": "PLANNED", "depends_on": [],
             "completion_evidence": ["docs/x.md"], "slices": []}
    derived = refill.derive(PORTFOLIO[0], HEAD, graph(PORTFOLIO[0], depends_on=("cap-first",), extra=[other]),
                            SOURCE, items=[], state={})

    assert derived["requests"] == []  # cap-cache waits for cap-first, which declares no executable slice
    assert derived["direction_required"][0]["reason"] == "EXECUTION_SLICE_REQUIRED"
    assert "cap-first" in json.dumps(derived["direction_required"][0]["evidence"])


def test_an_eligible_capability_without_a_bounded_slice_asks_a_focused_question() -> None:
    derived = refill.derive(PORTFOLIO[0], HEAD, graph(PORTFOLIO[0], slices=[]), SOURCE, items=[], state={})

    (row,) = derived["direction_required"]
    assert row["reason"] == "EXECUTION_SLICE_REQUIRED" and "what bounded slice" in row["question"]


def test_slice_dependencies_are_honoured_so_only_the_ready_slice_is_requested() -> None:
    slices = [{"id": "s2", "state": "PLANNED", "objective": "Second", "acceptance": ["b"], "depends_on": ["s1"],
               "allowed_paths": ["pkg/**"]},
              {"id": "s1", "state": "PLANNED", "objective": "First", "acceptance": ["a"], "allowed_paths": ["pkg/**"]}]
    derived = refill.derive(PORTFOLIO[0], HEAD, graph(PORTFOLIO[0], slices=slices), SOURCE, items=[], state={})

    assert [r["slice"] for r in derived["requests"]] == ["s1"]  # s2 is listed first but depends on s1


def test_a_completed_roadmap_needs_proof_before_it_stops_refilling() -> None:
    done = graph(PORTFOLIO[0], state="VERIFIED_COMPLETE")
    done["capabilities"][0]["verification"] = ["pytest"]
    done["roadmap"] = []
    unproven = refill.derive(PORTFOLIO[0], HEAD, done, SOURCE, items=[], state={})
    assert unproven["requests"] == [] and unproven["direction_required"][0]["reason"] == "PRODUCT_COMPLETION_EVIDENCE_REQUIRED"

    done["product_completion"] = {"verified": True, "exact_head": HEAD, "evidence": ["docs/PROJECT_STATUS.md"],
                                  "checks": [{"name": "product reality", "status": "PASS"}]}
    proven = refill.derive(PORTFOLIO[0], HEAD, done, SOURCE, items=[], state={})
    assert proven["requests"] == [] and proven["direction_required"] == [] and proven["skipped"][0]["reason"] == "ROADMAP_EXHAUSTED"


def test_a_slice_without_allowed_paths_is_direction_required_not_guessed() -> None:
    bare = [{"id": "s1", "state": "PLANNED", "objective": "x", "acceptance": ["y"]}]
    derived = refill.derive(PORTFOLIO[0], HEAD, graph(PORTFOLIO[0], slices=bare), SOURCE, items=[], state={})

    assert derived["requests"] == [] and derived["direction_required"][0]["reason"] == "ALLOWED_PATHS_MISSING"


def test_an_invalid_graph_is_direction_required() -> None:
    broken = graph(PORTFOLIO[0])
    broken["capabilities"][0]["completion_evidence"] = []
    derived = refill.derive(PORTFOLIO[0], HEAD, broken, SOURCE, items=[], state={})

    assert derived["requests"] == [] and derived["direction_required"][0]["reason"] == "CAPABILITY_GRAPH_INVALID"


def seeded_item(request: dict) -> dict:
    return refill.stamp(good_draft(request), request, SOURCE)


def test_in_flight_and_finished_slices_are_skipped_and_a_failed_one_asks_for_a_new_revision() -> None:
    request = request_for()
    item = seeded_item(request)

    def derive_with(state: dict) -> dict:
        return refill.derive(PORTFOLIO[0], HEAD, graph(PORTFOLIO[0]), SOURCE, items=[item], state=state)

    pending = derive_with({"items": {item["id"]: {"attempts": 1, "outcome": "DISPATCHED"}}})
    assert pending["requests"] == [] and pending["skipped"][0]["reason"] == "PENDING"
    merged = derive_with({"items": {item["id"]: {"attempts": 1, "outcome": "MERGED"}}})
    assert merged["requests"] == [] and merged["skipped"][0]["reason"] == "DONE"
    failed = derive_with({"items": {item["id"]: {"attempts": 1, "outcome": "FAIL: canonical preflight failed"}}})
    (again,) = failed["requests"]
    assert again["id"] == item["id"] + "-r1"  # a failed directed edit is re-authored under a new id
    assert again["prior_failures"] == [{"id": item["id"], "outcome": "FAIL: canonical preflight failed"}]


# --- admission gate ------------------------------------------------------------------------------------------


def test_a_sound_draft_has_no_problems() -> None:
    request = request_for()

    assert problems(good_draft(request), request) == []


@pytest.mark.parametrize("change,code", [
    ({"id": "RM-other"}, "ID_MISMATCH"),
    ({"repository": "jweter/VoiceStudio"}, "REPOSITORY_MISMATCH"),
    ({"roadmap_ref": "docs/anything.md"}, "ROADMAP_REF_MISMATCH"),
    ({"addresses": "something the roadmap never mentions"}, "NOT_TRACEABLE_TO_ROADMAP_OUTCOME"),
    ({"level": 3}, "LEVEL_BEYOND_LOCAL_BUILDER"),
    ({"interfaces": ["other/module.py"]}, "PATH_OUTSIDE_ROADMAP_SCOPE"),
    ({"interfaces": ["pkg/secret/key.py"]}, "PATH_OUTSIDE_ROADMAP_SCOPE"),
    ({"approved_base": ""}, "APPROVED_BASE_MISSING"),
    ({"consumer": "x"}, "CONSUMER_UNIDENTIFIED"),
    ({"tests": "nope"}, "MISSING_TESTS"),
])
def test_a_draft_that_breaks_the_contract_is_rejected_with_a_reason(change: dict, code: str) -> None:
    request = request_for()

    assert code in problems(good_draft(request, **change), request)


def test_a_draft_cannot_widen_scope_through_its_direct_edit() -> None:
    request = request_for()
    draft = good_draft(request)
    draft["direct"] = {**draft["direct"], "module_path": "scripts/unrelated.py"}

    assert "PATH_OUTSIDE_ROADMAP_SCOPE" in problems(draft, request)


def test_a_file_reserved_by_open_work_is_refused() -> None:
    request = request_for()

    assert "FILE_RESERVED_BY_OPEN_WORK" in problems(good_draft(request), request, reserved={"pkg/core.py"})


def test_a_stale_approved_base_is_refused_only_when_main_changed_the_files() -> None:
    request = request_for()
    older = good_draft(request, base="b" * 40)

    assert problems(older, request, changed=lambda *_a: {"README.md"}) == []
    assert "APPROVED_BASE_STALE" in problems(older, request, changed=lambda *_a: {"pkg/core.py"})

    def unreadable(*_args: str) -> set[str]:
        raise RuntimeError("no history")

    assert "APPROVED_BASE_UNVERIFIABLE" in problems(older, request, changed=unreadable)


def test_duplicate_objectives_and_targets_are_refused_but_retired_work_is_not_a_duplicate() -> None:
    request = request_for()
    existing = {**seeded_item(request), "id": "OTHER-1"}

    assert "DUPLICATE_OBJECTIVE" in problems(good_draft(request), request, items=[existing])
    renamed = good_draft(request, objective="Another wording of the same edit target.")
    assert "DUPLICATE_TARGET" in problems(renamed, request, items=[existing])
    assert problems(renamed, request, items=[{**existing, "status": "retired"}]) == []


def test_non_object_drafts_are_rejected() -> None:
    assert problems(["not", "an", "object"], request_for()) == ["DRAFT_NOT_AN_OBJECT"]


# --- end to end: real git repository -> request -> draft -> dispatchable backlog item ------------------------------


def lane_dirs(tmp_path: Path) -> tuple[Path, Path]:
    lane, state = tmp_path / "lane", tmp_path / "flow-state"
    lane.mkdir()
    state.mkdir()
    return lane, state


def run_refill(tmp_path: Path, repository: str, clone: Path, head: str, **kw) -> dict:
    lane, state = lane_dirs(tmp_path) if not (tmp_path / "lane").exists() else (tmp_path / "lane", tmp_path / "flow-state")
    args = {"items": [], "state": {}, "reserved": set(), "max_level": 2}
    args.update(kw)
    return refill.refill_repository(lane, state, repository, clone, head, **args)


def test_empty_backlog_refill_end_to_end_yields_a_dispatchable_item_without_fabricated_evidence(tmp_path: Path) -> None:
    repository = PORTFOLIO[0]
    clone, head = product(tmp_path, repository, graph(repository))

    first = run_refill(tmp_path, repository, clone, head)
    request = json.loads((tmp_path / "lane" / refill.REQUESTS / (refill.item_id(PORTFOLIO[0], "cap-cache", "s1") + ".json")).read_text("utf-8"))
    assert first["requests"] == [request["id"]] and first["admitted"] == [] and request["exact_head"] == head
    assert refill.admitted_items(tmp_path / "lane") == []  # a request alone grants nothing

    drafts = tmp_path / "lane" / refill.DRAFTS
    drafts.mkdir()
    (drafts / f"{request['id']}.json").write_text(json.dumps(good_draft(request, base=head)), encoding="utf-8")
    second = run_refill(tmp_path, repository, clone, head)

    assert second["admitted"] == [request["id"]]
    (item,) = refill.admitted_items(tmp_path / "lane")
    assert backlog.item_problems(item) == [] and item["origin"] == "roadmap-derived" and item["status"] == "approved"
    assert item["authorization"]["authority"] == SOURCE and item["authorization"]["exact_head"] == head
    assert "no execution" in item["authorization"]["grants"]
    state = backlog.load_state(tmp_path / "lane")
    assert backlog.next_item([item], state, repository, max_level=2) == item  # the existing dispatcher accepts it
    assert state.get("items", {}).get(item["id"]) is None  # nothing is recorded done or attempted
    assert not backlog.is_done(item, state)
    kinds = [row["kind"] for row in refill.load_store(tmp_path / "lane")["ledger"]]
    assert kinds == ["GENERATED", "ADMITTED"]


def test_a_rerun_is_idempotent_and_an_unchanged_rejected_draft_is_not_rejudged(tmp_path: Path) -> None:
    repository = PORTFOLIO[1]
    clone, head = product(tmp_path, repository, graph(repository))
    run_refill(tmp_path, repository, clone, head)
    request = json.loads(next((tmp_path / "lane" / refill.REQUESTS).glob("*.json")).read_text("utf-8"))
    drafts = tmp_path / "lane" / refill.DRAFTS
    drafts.mkdir()
    (drafts / f"{request['id']}.json").write_text(json.dumps(good_draft(request, level=4)), encoding="utf-8")

    first = run_refill(tmp_path, repository, clone, head)
    again = run_refill(tmp_path, repository, clone, head)

    assert first["blocked"] == [request["id"]] and again["blocked"] == [] and again["rejected"] == []
    ledger = refill.load_store(tmp_path / "lane")["ledger"]
    assert [row["kind"] for row in ledger].count("CAPABILITY_BLOCKER") == 1  # recorded, not reinterpreted
    assert refill.admitted_items(tmp_path / "lane") == []
    assert [row["kind"] for row in ledger].count("GENERATED") == 1


def test_a_corrected_draft_is_judged_again_and_admitted_once(tmp_path: Path) -> None:
    repository = PORTFOLIO[2]
    clone, head = product(tmp_path, repository, graph(repository))
    run_refill(tmp_path, repository, clone, head)
    request = json.loads(next((tmp_path / "lane" / refill.REQUESTS).glob("*.json")).read_text("utf-8"))
    drafts = tmp_path / "lane" / refill.DRAFTS
    drafts.mkdir()
    path = drafts / f"{request['id']}.json"
    path.write_text(json.dumps(good_draft(request, roadmap_ref="wrong")), encoding="utf-8")
    assert run_refill(tmp_path, repository, clone, head)["rejected"] == [request["id"]]

    path.write_text(json.dumps(good_draft(request, base=head)), encoding="utf-8")
    assert run_refill(tmp_path, repository, clone, head)["admitted"] == [request["id"]]
    assert run_refill(tmp_path, repository, clone, head)["admitted"] == []
    assert len(refill.admitted_items(tmp_path / "lane")) == 1


def test_all_six_product_repositories_get_independent_requests_and_dispatchable_items(tmp_path: Path) -> None:
    items = []
    for repository in PORTFOLIO:
        clone, head = product(tmp_path / repository.split("/")[1], repository, graph(repository))
        lane = tmp_path / "lane-all"
        lane.mkdir(exist_ok=True)
        (tmp_path / "state-all").mkdir(exist_ok=True)
        report = refill.refill_repository(lane, tmp_path / "state-all", repository, clone, head, items=items,
                                          state={}, reserved=set(), max_level=2)
        (request_id,) = report["requests"]
        request = json.loads((lane / refill.REQUESTS / f"{request_id}.json").read_text("utf-8"))
        drafts = lane / refill.DRAFTS
        drafts.mkdir(exist_ok=True)
        (drafts / f"{request_id}.json").write_text(json.dumps(good_draft(
            request, base=head, objective=f"Distinct bounded objective for {repository}.")), encoding="utf-8")
        refill.refill_repository(lane, tmp_path / "state-all", repository, clone, head, items=items, state={},
                                 reserved=set(), max_level=2)
        items = refill.admitted_items(lane)
    lane = tmp_path / "lane-all"

    assert sorted(item["repository"] for item in items) == sorted(PORTFOLIO)
    assert len({item["id"] for item in items}) == 6
    for repository in PORTFOLIO:  # fair progression: the existing per-repository selector serves each independently
        chosen = backlog.next_item(items, {}, repository, max_level=2)
        assert chosen is not None and chosen["repository"] == repository
    assert [row["kind"] for row in refill.load_store(lane)["ledger"]].count("ADMITTED") == 6


@pytest.mark.parametrize("scenario", ["undeclared", "not_authoritative", "wrong_repository", "missing_file"])
def test_missing_or_conflicting_roadmap_authority_is_direction_required_and_never_admits(tmp_path: Path, scenario: str) -> None:
    repository = PORTFOLIO[3]
    payload = graph("jweter/someone-else" if scenario == "wrong_repository" else repository)
    clone, head = product(tmp_path, repository, None if scenario == "missing_file" else payload,
                          declare=scenario != "undeclared")
    if scenario == "not_authoritative":
        plane = {"schema_version": 1, "capability_graph": SOURCE, "authoritative_documents": ["docs/other.md"]}
        (clone / "engineering" / "control-plane.json").write_text(json.dumps(plane), encoding="utf-8")
        run_git(clone, "commit", "-qam", "plane")
        head = run_git(clone, "rev-parse", "HEAD")

    report = run_refill(tmp_path, repository, clone, head)

    assert report["requests"] == [] and report["admitted"] == []
    (row,) = report["direction_required"]
    assert row["question"] and row["reason"]
    assert [e["kind"] for e in refill.load_store(tmp_path / "lane")["ledger"]] == ["DIRECTION_REQUIRED"]
    assert refill.admitted_items(tmp_path / "lane") == []


def test_authority_is_read_at_the_exact_head_not_the_working_tree(tmp_path: Path) -> None:
    repository = PORTFOLIO[4]
    clone, head = product(tmp_path, repository, graph(repository))
    (clone / SOURCE).write_text(json.dumps(graph(repository, state="BLOCKED")), encoding="utf-8")  # dirty edit

    report = run_refill(tmp_path, repository, clone, head)

    assert report["requests"] != [] and report["direction_required"] == []


def test_a_repeated_direction_question_is_logged_once(tmp_path: Path) -> None:
    repository = PORTFOLIO[5]
    clone, head = product(tmp_path, repository, graph(repository, slices=[]))

    run_refill(tmp_path, repository, clone, head)
    run_refill(tmp_path, repository, clone, head)

    assert [e["kind"] for e in refill.load_store(tmp_path / "lane")["ledger"]] == ["DIRECTION_REQUIRED"]


# --- store integrity, reservations, recovery, scheduling ---------------------------------------------------------


def test_tampered_or_colliding_runtime_items_are_never_dispatched(tmp_path: Path) -> None:
    request = request_for()
    good = seeded_item(request)
    no_origin = {k: v for k, v in good.items() if k != "origin"} | {"id": "RM-x"}
    malformed = {**good, "id": "RM-y", "consumer": ""}
    release = {**good, "origin": "owner"}
    lane = tmp_path
    refill.store_path(lane).write_text(json.dumps({"items": [good, no_origin, malformed, "junk"]}), encoding="utf-8")

    assert [i["id"] for i in refill.admitted_items(lane)] == [good["id"]]
    assert refill.admitted_items(lane, existing=[release]) == []  # the release backlog wins an id collision


def test_a_crash_inside_a_store_update_leaves_the_last_whole_file(tmp_path: Path) -> None:
    def add(store: dict) -> None:
        store["ledger"].append({"kind": "ONE"})

    refill._mutate(tmp_path, add)

    def crash(store: dict) -> None:
        store["ledger"].append({"kind": "TWO"})
        raise RuntimeError("power loss")

    with pytest.raises(RuntimeError):
        refill._mutate(tmp_path, crash)

    assert [row["kind"] for row in refill.load_store(tmp_path)["ledger"]] == ["ONE"]
    refill._mutate(tmp_path, add)  # the lock was released, so the restarted engine can continue
    assert [row["kind"] for row in refill.load_store(tmp_path)["ledger"]] == ["ONE", "ONE"]


def test_a_corrupt_store_reads_as_empty_rather_than_blocking_the_lane(tmp_path: Path) -> None:
    refill.store_path(tmp_path).write_text("{not json", encoding="utf-8")

    assert refill.load_store(tmp_path)["items"] == [] and refill.admitted_items(tmp_path) == []


def test_the_ledger_is_bounded(tmp_path: Path) -> None:
    for number in range(refill.LEDGER_LIMIT + 40):
        refill._mutate(tmp_path, lambda store, n=number: refill._log(store, "GENERATED", "r", str(n), {}))

    ledger = refill.load_store(tmp_path)["ledger"]
    assert len(ledger) == refill.LEDGER_LIMIT and ledger[-1]["id"] == str(refill.LEDGER_LIMIT + 39)


def test_authority_is_rechecked_on_an_interval_or_when_a_draft_waits(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    assert refill.due(tmp_path, PORTFOLIO[0], now=now)
    refill._mutate(tmp_path, lambda store: store["checked"].update({PORTFOLIO[0]: {"at": now.isoformat(), "head": HEAD}}))
    assert not refill.due(tmp_path, PORTFOLIO[0], now=now + timedelta(minutes=5))
    assert refill.due(tmp_path, PORTFOLIO[0], now=now + timedelta(seconds=refill.CHECK_INTERVAL_SECONDS + 1))
    drafts = tmp_path / refill.DRAFTS
    drafts.mkdir()
    (drafts / (refill.id_prefix(PORTFOLIO[0]) + "anything.json")).write_text("{}", encoding="utf-8")
    assert refill.due(tmp_path, PORTFOLIO[0], now=now + timedelta(minutes=5))
    assert refill.due(tmp_path, PORTFOLIO[1], now=now)  # never checked
    refill._mutate(tmp_path, lambda store: store["checked"].update({PORTFOLIO[1]: {"at": now.isoformat(), "head": HEAD}}))
    assert not refill.due(tmp_path, PORTFOLIO[1], now=now + timedelta(minutes=5))  # a draft for another repo is irrelevant


def test_the_open_publication_reservation_blocks_admission_end_to_end(tmp_path: Path) -> None:
    repository = PORTFOLIO[0]
    clone, head = product(tmp_path, repository, graph(repository))
    run_refill(tmp_path, repository, clone, head)
    request = json.loads(next((tmp_path / "lane" / refill.REQUESTS).glob("*.json")).read_text("utf-8"))
    drafts = tmp_path / "lane" / refill.DRAFTS
    drafts.mkdir()
    (drafts / f"{request['id']}.json").write_text(json.dumps(good_draft(request, base=head)), encoding="utf-8")

    report = run_refill(tmp_path, repository, clone, head, reserved={"pkg/core.py"})

    assert report["rejected"] == [request["id"]] and refill.admitted_items(tmp_path / "lane") == []
    assert "FILE_RESERVED_BY_OPEN_WORK" in refill.load_store(tmp_path / "lane")["ledger"][-1]["detail"]["problems"]


def test_the_owner_policy_enables_refill_and_the_module_grants_no_authority() -> None:
    policy = json.loads((Path(__file__).resolve().parents[1] / "engineering" / "iron-god-commissioning.json")
                        .read_text(encoding="utf-8"))["portfolio_rotation"]
    assert policy["roadmap_refill"] is True and policy["planning"] == "backlog"
    source = (Path(refill.__file__)).read_text(encoding="utf-8")
    for forbidden in ("owner_control", "Enable-ScheduledTask", "gh pr merge", "git push", "branch -D"):
        assert forbidden not in source


def test_drafts_are_not_mutated_by_admission() -> None:
    request = request_for()
    draft = good_draft(request)
    before = copy.deepcopy(draft)

    refill.stamp(draft, request, SOURCE)
    problems(draft, request)

    assert draft == before


# --- review findings on #595: revision duplicates and capability identity ------------------------------------


FAILED = {"attempts": 1, "outcome": "FAIL: canonical preflight failed"}


def failed_chain(count: int = 1) -> tuple[list[dict], dict]:
    """The first request's item plus (count - 1) admitted revisions, every one failed."""
    first = request_for()
    items, state = [seeded_item(first)], {"items": {}}
    state["items"][items[0]["id"]] = dict(FAILED)
    for number in range(1, count):
        derived = refill.derive(PORTFOLIO[0], HEAD, graph(PORTFOLIO[0]), SOURCE, items=items, state=state)
        (request,) = derived["requests"]
        assert request["id"] == f"{first['id']}-r{number}"
        revision = seeded_item(request)
        items.append(revision)
        state["items"][revision["id"]] = dict(FAILED)
    return items, state


def test_slice_ids_include_the_capability_and_survive_sanitization_and_truncation() -> None:
    repository = PORTFOLIO[0]
    ids = {
        refill.item_id(repository, "cap-a", "s1"), refill.item_id(repository, "cap-b", "s1"),
        refill.item_id(repository, "cap a", "s1"), refill.item_id(repository, "cap-a", "s 1"),
        refill.item_id(repository, "x" * 90, "s1"), refill.item_id(repository, "x" * 90 + "y", "s1"),
        refill.item_id(PORTFOLIO[1], "cap-a", "s1")}

    assert len(ids) == 7  # same slice id under another capability, odd characters, long names: all distinct
    assert refill.item_id(repository, "cap-a", "s1") == refill.item_id(repository, "cap-a", "s1")  # and stable
    assert all(len(i) <= 90 and i.startswith(refill.id_prefix(repository)) or i.startswith(refill.id_prefix(PORTFOLIO[1]))
               for i in ids)


def test_a_second_capability_reusing_a_slice_id_does_not_inherit_the_first_ones_completion() -> None:
    first = request_for()  # cap-cache / s1
    merged = seeded_item(first)
    payload = graph(PORTFOLIO[0], state="VERIFIED_COMPLETE")
    payload["capabilities"][0]["verification"] = ["pytest"]
    payload["capabilities"].append({
        "id": "cap-next", "capability": "Next outcome", "state": "PLANNED", "depends_on": ["cap-cache"],
        "completion_evidence": ["docs/ROADMAP.md next"],
        "slices": [{"id": "s1", "state": "PLANNED", "objective": "Different work under the same local slice id.",
                    "acceptance": ["it works"], "allowed_paths": ["pkg/**"]}]})
    payload["roadmap"] = ["cap-next"]

    derived = refill.derive(PORTFOLIO[0], HEAD, payload, SOURCE, items=[merged],
                            state={"items": {merged["id"]: {"attempts": 1, "outcome": "MERGED"}}})

    (request,) = derived["requests"]
    assert request["capability"] == "cap-next" and request["id"] != merged["id"]
    assert derived["skipped"] == []  # not silently treated as DONE


def test_a_corrected_draft_for_failed_work_is_admitted_even_with_the_same_objective_and_target() -> None:
    items, state = failed_chain()
    derived = refill.derive(PORTFOLIO[0], HEAD, graph(PORTFOLIO[0]), SOURCE, items=items, state=state)
    (request,) = derived["requests"]
    same = good_draft(request)  # identical objective and direct target as the failed predecessor
    reworded = good_draft(request, objective="A corrected wording aimed at the same named unit.")

    assert request["prior_failures"][0]["id"] == items[0]["id"]
    assert problems(same, request, items=items) == []
    assert problems(reworded, request, items=items) == []


def test_every_failed_predecessor_is_exempt_but_unrelated_and_active_work_still_counts() -> None:
    items, state = failed_chain(count=3)
    derived = refill.derive(PORTFOLIO[0], HEAD, graph(PORTFOLIO[0]), SOURCE, items=items, state=state)
    (request,) = derived["requests"]
    assert request["id"].endswith("-r3") and len(request["prior_failures"]) == 3
    assert problems(good_draft(request), request, items=items) == []

    unrelated = {**seeded_item(request_for()), "id": "OWNER-ITEM", "status": "approved"}
    assert "DUPLICATE_OBJECTIVE" in problems(good_draft(request), request, items=[*items, unrelated])
    other_target = {**unrelated, "objective": "Something else entirely."}
    assert "DUPLICATE_TARGET" in problems(good_draft(request), request, items=[*items, other_target])


def test_the_re_authoring_path_end_to_end_admits_the_revision_once(tmp_path: Path) -> None:
    repository = PORTFOLIO[0]
    clone, head = product(tmp_path, repository, graph(repository))
    failed_request = refill.derive(repository, head, graph(repository), SOURCE, items=[], state={})["requests"][0]
    failed = refill.stamp(good_draft(failed_request, base=head), failed_request, SOURCE)
    state = {"items": {failed["id"]: dict(FAILED)}}
    first = run_refill(tmp_path, repository, clone, head, items=[failed], state=state)
    (request_id,) = first["requests"]
    assert request_id == failed["id"] + "-r1"
    request = json.loads((tmp_path / "lane" / refill.REQUESTS / f"{request_id}.json").read_text("utf-8"))
    drafts = tmp_path / "lane" / refill.DRAFTS
    drafts.mkdir()
    (drafts / f"{request_id}.json").write_text(json.dumps(good_draft(request, base=head)), encoding="utf-8")

    second = run_refill(tmp_path, repository, clone, head, items=[failed], state=state)

    assert second["admitted"] == [request_id] and second["rejected"] == []
    (admitted,) = refill.admitted_items(tmp_path / "lane", existing=[failed])
    assert admitted["id"] == request_id
    again = run_refill(tmp_path, repository, clone, head, items=[failed, admitted], state=state)
    assert again["requests"] == []  # the revision is now pending; nothing is re-requested
