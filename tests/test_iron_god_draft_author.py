"""The draft-author runner records one planning result and never passes on doubt."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import iron_god_draft_author as author

REQUEST = {"id": "RM-x", "repository": "jweter/VoiceStudio", "roadmap_ref": "g#c/s", "objective": "Keep the newest entries.",
           "slice_acceptance": ["returns the last N items"], "allowed_paths": ["pkg/**", "tests/**"],
           "forbidden_paths": ["pkg/secret/**"], "prior_failures": [], "reserved_conflicts": ["pkg/core.py"]}


def go(tmp_path: Path, **kw) -> dict:
    kw.setdefault("planner", lambda *_a: {"function_name": "f"})
    kw.setdefault("validator", lambda raw, *_a: {"function_name": raw["function_name"]})
    kw.setdefault("authority", lambda: True)
    return author.run(REQUEST, tmp_path, "a" * 40, tmp_path / "out" / "r.json", roots=["pkg/"], forbidden=[".github/"],
                      attempted=["old_fn"], **kw)


def test_the_guide_carries_the_roadmap_slice_its_fences_and_earlier_failures() -> None:
    text = author.guide_text({**REQUEST, "prior_failures": [{"id": "RM-x", "outcome": "FAIL"}], "planner_feedback": "BAD_PATH"})

    for needed in ("RM-x", "g#c/s", "Keep the newest entries.", "returns the last N items", "pkg/**, tests/**",
                   "Never plan anything under: pkg/secret/**", "Earlier attempts failed", "rejected for: BAD_PATH"):
        assert needed in text


def test_the_packet_scopes_planning_to_the_allowed_roots_and_the_reserved_files() -> None:
    packet = author.build_packet(REQUEST, ["pkg/"], "b" * 40, [".github/"], ["old_fn"])

    assert packet["paths"] == ["pkg/"] and packet["avoid_paths"] == ["pkg/core.py"] and packet["base_sha"] == "b" * 40
    assert packet["planning"]["forbidden_paths"] == [".github/"] and packet["attempted_functions"] == ["old_fn"]


def test_a_valid_plan_is_recorded_with_the_raw_and_validated_forms(tmp_path: Path) -> None:
    result = go(tmp_path)

    on_disk = json.loads((tmp_path / "out" / "r.json").read_text(encoding="utf-8"))
    assert result["ok"] is True and on_disk["plan"] == {"function_name": "f"} and on_disk["validated"] == {"function_name": "f"}
    assert on_disk["base"] == "a" * 40 and "finished_at" in on_disk


def test_the_planner_receives_the_roadmap_guide_as_its_feedback(tmp_path: Path) -> None:
    seen = {}

    def planner(clone, packet, check, feedback):
        seen.update(feedback=feedback, packet=packet)
        check()
        return {"function_name": "f"}

    go(tmp_path, planner=planner)

    assert "ROADMAP SLICE (mandatory)" in seen["feedback"] and seen["packet"]["paths"] == ["pkg/"]


def test_a_rejected_plan_a_model_error_and_a_revoked_authority_are_all_failed_results(tmp_path: Path) -> None:
    def reject(*_a):
        raise ValueError("PLAN_PATH_OUTSIDE_ENVELOPE")

    def explode(*_a):
        raise OSError("ollama unreachable")

    assert "PLAN_PATH_OUTSIDE_ENVELOPE" in go(tmp_path, validator=reject)["reason"]
    assert "ollama unreachable" in go(tmp_path, planner=explode)["reason"]
    revoked = go(tmp_path, authority=lambda: False)
    assert revoked["ok"] is False and "EXECUTION_AUTHORITY_REVOKED" in revoked["reason"]
    assert (tmp_path / "out" / "r.json").is_file()
