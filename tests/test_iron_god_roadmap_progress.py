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


class Publisher:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, repository: str, branch: str, title: str, body: str) -> str:
        self.calls.append((repository, branch, title, body))
        return f"https://github.com/{repository}/pull/99"


def record(world: dict, items, state, publications, publisher=None, **kw) -> dict:
    return progress.record_progress(world["lane"], world["state_root"], REPO, world["clone"], world["head"], items=items,
                                    state=state, publications=publications, workspace=world["tmp"] / "scratch",
                                    publisher=publisher or Publisher(), **kw)


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


# --- the pull request -------------------------------------------------------------------------------------------


def test_a_merged_slice_becomes_one_verified_pull_request_that_touches_only_the_graph(world: dict) -> None:
    items, state, publications = merged_item(world)
    publisher = Publisher()

    result = record(world, items, state, publications, publisher)

    assert result["status"] == "OPENED" and result["pr"].endswith("/pull/99") and result["slices"] == ["s1"]
    ((repository, branch, title, body),) = publisher.calls
    assert repository == REPO and branch.startswith(progress.BRANCH_PREFIX) and "record 1 merged slice" in title
    assert "pull/7 merged as" in body and "Only `state` and `verification`" in body
    pushed = run(world["origin"], "rev-parse", f"refs/heads/{branch}")
    assert run(world["origin"], "diff", "--name-only", world["head"], pushed).split() == [SOURCE]
    edited = json.loads(run(world["origin"], "show", f"{pushed}:{SOURCE}"))
    assert edited["capabilities"][0]["slices"][0]["state"] == "VERIFIED_COMPLETE"
    assert edited["capabilities"][0]["slices"][1]["state"] == "PLANNED"  # s2 is untouched
    assert edited["capabilities"][0]["state"] == "PLANNED"  # a capability is never completed here


def test_the_same_progress_is_never_proposed_twice(world: dict) -> None:
    items, state, publications = merged_item(world)
    publisher = Publisher()
    first = record(world, items, state, publications, publisher)

    second = record(world, items, state, publications, publisher)

    assert first["status"] == "OPENED" and second["status"] == "ALREADY_OPEN" and len(publisher.calls) == 1


def test_nothing_merged_means_nothing_is_published(world: dict) -> None:
    publisher = Publisher()

    result = record(world, [], {}, {}, publisher)

    assert result["status"] == "NOTHING_TO_RECORD" and publisher.calls == []


def test_a_non_canonical_graph_is_a_blocker_and_publishes_nothing(world: dict) -> None:
    items, state, publications = merged_item(world)
    (world["clone"] / SOURCE).write_text(json.dumps(graph_payload()) + "\n", encoding="utf-8", newline="\n")
    run(world["clone"], "commit", "-qam", "compact graph")
    run(world["clone"], "push", "-q", "origin", "main")
    world["head"] = run(world["clone"], "rev-parse", "HEAD")
    refill.refill_repository(world["lane"], world["state_root"], REPO, world["clone"], world["head"], items=[], state={},
                             reserved=set(), max_level=2)
    publisher = Publisher()

    result = record(world, items, state, publications, publisher)

    assert result == {"status": "BLOCKED", "reason": "GRAPH_FORMAT_NOT_CANONICAL"} and publisher.calls == []


def test_a_graph_compiled_for_a_different_head_is_not_used(world: dict) -> None:
    items, state, publications = merged_item(world)
    world["head"] = "f" * 40

    assert record(world, items, state, publications)["status"] == "GRAPH_NOT_AT_HEAD"


def test_a_missing_compiled_graph_is_reported_not_guessed(world: dict) -> None:
    for path in (world["state_root"] / "capability-graphs").glob("*.json"):
        path.unlink()

    assert record(world, [], {}, {})["status"] == "NO_COMPILED_GRAPH"


def test_the_writer_never_merges_or_touches_owner_controls() -> None:
    source = Path(progress.__file__).read_text(encoding="utf-8")
    for forbidden in ("gh pr merge", "Enable-ScheduledTask", "owner_control", "branch -D", "--force", "reset --hard"):
        assert forbidden not in source
