from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_product_lane as product_lane

ROADMAP = "docs/OWNER_APPROVED_ROADMAP_2026-10-25.md"
APPROVED_TEXT = ("# Roadmap\n\nStatus: OWNER-APPROVED\n\n## Standing development authorization\n\nThis roadmap authorizes development.\n\n"
                 "## Next slices\n\n1. Inventory the mechanics.\n")


def item(item_id, repo="jweter/Project-Everward", *, status="proposed", ref=f"{ROADMAP} next slice 1", level=3, **over):
    base = {"id": item_id, "repository": repo, "title": f"{item_id} title", "roadmap_ref": ref, "addresses": "a gap",
            "why": "needed", "consumer": "the loop verification step", "objective": "do it", "interfaces": [], "depends_on": [],
            "acceptance_criteria": ["works"], "tests": [], "level": level, "complexity": "low", "verification": "preflight",
            "definition_of_done": "merged", "status": status}
    base.update(over)
    return base


VERIFIED = {"jweter/Project-Everward": {ROADMAP}}


def test_a_proposed_item_that_cites_the_owners_verified_roadmap_is_approved_by_it_without_asking_again() -> None:
    out = backlog.promote_by_roadmap([item("EW-1")], VERIFIED)[0]
    assert out["status"] == "approved" and out["assignee"] == backlog.AGENT_ID and out["approved_by"] == f"OWNER_ROADMAP:{ROADMAP}"
    assert backlog.item_problems(out) == []


def test_the_roadmap_reference_must_be_a_real_verified_owner_roadmap_of_that_repository() -> None:
    unchanged = [
        item("EW-A", ref="Iron God readiness check 2026-10-27 (engine-originated audit finding)"),       # not the owner's roadmap
        item("EW-B", ref="docs/ROADMAP.md next slice 1"),                                                 # an ordinary doc, not owner-approved
        item("EW-C", ref="docs/OWNER_APPROVED_ROADMAP_2099-01-01.md next slice 1"),                       # a path nobody verified
        item("EW-D", repo="jweter/VoiceStudio"),                                                          # verified for another repository
        item("EW-E", ref=f"{ROADMAP}.evil next slice 1"),                                                 # a look-alike path
    ]
    for original, result in zip(unchanged, backlog.promote_by_roadmap(unchanged, VERIFIED)):
        assert result == original and result["status"] == "proposed", original["id"]
    assert backlog.promote_by_roadmap([item("EW-1")], {}) [0]["status"] == "proposed"                     # nothing verified: nothing approved


def test_retired_done_and_malformed_items_are_never_promoted() -> None:
    items = [item("R", status="retired"), item("D", status="done"), item("A", status="approved"),
             item("L1", level=1)]                                                                         # approved level 1 needs a build spec
    out = {i["id"]: i for i in backlog.promote_by_roadmap(items, VERIFIED)}
    assert out["R"]["status"] == "retired" and out["D"]["status"] == "done" and out["A"].get("approved_by") is None
    assert out["L1"]["status"] == "proposed"                                                               # would be malformed once approved


def test_a_promoted_item_stops_being_excluded_as_not_approved_and_is_judged_on_its_real_constraints() -> None:
    state: dict = {}
    raw = [item("EW-1", level=3)]
    assert backlog.exclusion(raw[0], state, items=raw, done=set(), max_level=2)[0] == "NOT_APPROVED"      # the old, wrong reason
    promoted = backlog.promote_by_roadmap(raw, VERIFIED)
    reason = backlog.exclusion(promoted[0], state, items=promoted, done=set(), max_level=2)
    assert reason is not None and reason[0] == "LEVEL_ABOVE_MEASURED_CAPABILITY"                           # the true reason is now shown


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": __import__("os").environ["PATH"], "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})


def clone_with(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def test_the_lane_verifies_the_roadmap_from_the_document_itself(tmp_path: Path) -> None:
    clones = {
        "jweter/Project-Everward": clone_with(tmp_path, "ew", {ROADMAP: APPROVED_TEXT}),
        "jweter/NoStatus": clone_with(tmp_path, "ns", {ROADMAP: "# Roadmap\n\nStatus: DRAFT\n\nstanding development authorization\n"}),
        "jweter/NoGrant": clone_with(tmp_path, "ng", {ROADMAP: "# Roadmap\n\nStatus: OWNER-APPROVED\n\nnothing else\n"}),
        "jweter/NoRoadmap": clone_with(tmp_path, "nr", {"docs/README.md": "x"}),
    }
    lane = SimpleNamespace(clone=lambda repo: clones[repo] if repo in clones else tmp_path / "missing")
    found = product_lane.verified_owner_roadmaps(lane, [*clones, "jweter/NotCloned"])
    assert found == {"jweter/Project-Everward": {ROADMAP}}                                                 # only the genuine, granted one


def test_the_whole_chain_makes_the_owners_roadmap_items_eligible_without_any_owner_step(tmp_path: Path) -> None:
    clones = {"jweter/Project-Everward": clone_with(tmp_path, "ew", {ROADMAP: APPROVED_TEXT})}
    lane = SimpleNamespace(clone=lambda repo: clones[repo])
    raw = [item("EW-L2", level=2, direct={"module_path": "pkg/core.py", "targets": ["one"], "add": [], "direction": "make one() return 2",
                                        "test_path": "tests/test_one.py", "test_source": "from pkg.core import one\n\ndef test_one():\n    assert one() == 2\n"})]
    verified = product_lane.verified_owner_roadmaps(lane, ["jweter/Project-Everward"])
    approved = backlog.promote_by_roadmap(raw, verified)
    assert backlog.next_item(raw, {}, "jweter/Project-Everward", max_level=2) is None                      # before: stuck on approval
    chosen = backlog.next_item(approved, {}, "jweter/Project-Everward", max_level=2)
    assert chosen is not None and chosen["id"] == "EW-L2" and chosen["approved_by"].startswith("OWNER_ROADMAP:")


def test_a_memory_stall_in_local_decomposition_or_drafting_feeds_the_standing_dev_mode_rule(tmp_path, monkeypatch) -> None:
    """The roadmap reaches the local builder through decomposition and drafting; a RAM stall there is a development stall."""
    lane = product_lane.ProductLane(tmp_path)
    seen = []
    monkeypatch.setattr(product_lane, "_dev_mode_tick", lambda lane_, payload: seen.append(payload))
    monkeypatch.setattr(product_lane, "available_gib", lambda: 5.6)
    product_lane.log_draft_outcomes(lane, "jweter/knowledge-engine-core",
                                    [{"id": "RM-x", "status": "WAITING_FOR_MEMORY", "reason": "ModelNotAdmitted: MEMORY"}],
                                    prefix="LOCAL_DECOMPOSE_")
    assert len(seen) == 1 and seen[0]["status"] == "WAITING_FOR_MEMORY" and seen[0]["work_waiting"] is True
    assert seen[0]["route"]["required_gib"] == pytest.approx(7.3) and seen[0]["route"]["available_gib"] == 5.6   # the primary builder's need
    seen.clear()
    product_lane.log_draft_outcomes(lane, "jweter/knowledge-engine-core", [{"id": "RM-y", "status": "FAILED", "reason": "PLAN_REJECTED"}],
                                    prefix="LOCAL_DECOMPOSE_")
    assert seen == []                                                                                            # only a memory wait counts
