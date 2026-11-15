"""#710: concrete owner API names are verified local oracles, not cloud-bound gaps."""

from __future__ import annotations

from copy import deepcopy

import pytest

from project_orchestrator import iron_god_local_drafter as drafter
from scripts.iron_god_draft_author import guide_text


def request(calls: list[str], *, expected: list[object] | None = None) -> dict:
    return {
        "id": "RM-owner-named-contract",
        "repository": "jweter/VoiceStudio",
        "capability": "singing",
        "slice": "s5-full-render-codec-args",
        "roadmap_ref": "engineering/capability-graph.json#singing/s5-full-render-codec-args",
        "objective": "Map each audio export format to the prescribed codec arguments.",
        "slice_acceptance": ["WAV uses PCM", "MP3 uses libmp3lame"],
        "allowed_paths": ["backend/services/**", "tests/backend/services/**"],
        "forbidden_paths": [".github/**"],
        "route": {"builder": "LOCAL"},
        "capability_level": 1,
        "acceptance_examples": [
            {"call": call, "expected": value, "why": f"owner-defined case {i}"}
            for i, (call, value) in enumerate(zip(
                calls,
                expected if expected is not None else list(range(len(calls))),
                strict=True,
            ))
        ],
    }


VOICE_CALLS = [
    "singing_export_codec_args('wav')",
    "singing_export_codec_args('MP3')",
    "singing_export_codec_args(' flac ')",
    "singing_export_codec_args('ogg')",
]
VOICE_EXPECTED = [
    ["-c:a", "pcm_s16le"],
    ["-c:a", "libmp3lame", "-q:a", "2"],
    ["-c:a", "flac"],
    ["-c:a", "aac", "-b:a", "192k"],
]
AI_CALLS = [
    'build_citation_provenance_index([{"source_id": "s1", "title": "A"}], '
    '[{"excerpt_id": "e1", "source_id": "s1"}])',
    'build_citation_provenance_index([], [{"excerpt_id": "missing", "source_id": "s1"}])',
    'build_citation_provenance_index([{"source_id": "s1", "title": None}], '
    '[{"excerpt_id": "e2", "source_id": "s1"}])',
]


def test_voice_owner_oracles_are_local_instead_of_cloud_fallback() -> None:
    item = request(VOICE_CALLS, expected=VOICE_EXPECTED)
    parsed = drafter.roadmap_examples(item)
    assert drafter.owner_function_name(item) == "singing_export_codec_args"
    assert drafter.eligible(item) is True
    assert drafter.missing_examples(item) is False
    assert len(parsed) == 4
    assert parsed[0] == {
        "call": "{fn}('wav')", "expected": VOICE_EXPECTED[0],
        "why": "owner-defined case 0",
    }
    guidance = guide_text(item)
    assert "MANDATORY FUNCTION NAME: singing_export_codec_args" in guidance
    assert "Do not rename the owner-approved API" in guidance


def test_ai_owner_oracles_preserve_literal_dictionaries_and_none() -> None:
    item = request(AI_CALLS, expected=[{"e1": "source"}, {}, {"e2": None}])
    assert drafter.owner_function_name(item) == "build_citation_provenance_index"
    assert drafter.eligible(item) is True
    parsed = drafter.roadmap_examples(item)
    assert len(parsed) == 3
    assert parsed[2]["call"].startswith("{fn}(")
    assert '"title": None' in parsed[2]["call"]
    assert parsed[2]["expected"] == {"e2": None}


@pytest.mark.parametrize("mutation", [
    lambda x: x["acceptance_examples"][2].update(call="another_name('flac')"),
    lambda x: x["acceptance_examples"][2].update(call="{fn}('flac')"),
    lambda x: x["acceptance_examples"][2].update(call="singing_export_codec_args(os.getenv('X'))"),
    lambda x: x["acceptance_examples"][2].update(call="pkg.singing_export_codec_args('flac')"),
    lambda x: x["acceptance_examples"][2].update(why=""),
    lambda x: x["acceptance_examples"][2].update(
        call="singing_export_codec_args('MP3')",
    ),
])
def test_named_contract_is_rejected_when_mixed_unsafe_incomplete_or_duplicate(mutation) -> None:
    item = request(VOICE_CALLS)
    mutation(item)
    assert drafter.roadmap_examples(item) == []
    assert drafter.owner_function_name(item) is None
    assert drafter.eligible(item) is False
    assert drafter.missing_examples(item) is True


def test_named_owner_oracles_need_three_distinct_cases() -> None:
    item = request(VOICE_CALLS[:2])
    assert not drafter.eligible(item)
    assert drafter.roadmap_examples(item) == []


def test_unverified_self_authored_named_calls_cannot_claim_owner_approval() -> None:
    item = request(VOICE_CALLS)
    item["decomposition"] = {"oracle": "SELF_AUTHORED"}
    assert not drafter.eligible(item)
    assert drafter.roadmap_examples(item) == []


def test_two_legacy_owner_templates_remain_backward_compatible() -> None:
    item = request(["{fn}(1)", "{fn}(2)"])
    assert drafter.eligible(item)
    assert drafter.owner_function_name(item) is None
    assert len(drafter.roadmap_examples(item)) == 2


def test_owner_function_name_cannot_be_renamed_by_local_planner() -> None:
    item = request(VOICE_CALLS)
    validated = {"function_name": "other_name"}
    with pytest.raises(drafter.ContradictsRoadmap, match="OWNER_FUNCTION_NAME_MISMATCH"):
        drafter.plan_to_draft(item, {"examples": []}, validated)


def test_named_calls_produce_exact_immutable_draft_oracles() -> None:
    item = request(VOICE_CALLS, expected=VOICE_EXPECTED)
    validated = {
        "function_name": "singing_export_codec_args",
        "module_path": "backend/services/singing_export_format.py",
        "test_path": "tests/backend/services/test_singing_export_format.py",
        "signature": "def singing_export_codec_args(fmt: str) -> list[str]:",
        "objective": "Return exact codec options for given audio format.",
        "examples": [],
        "acceptance_criteria": ["returns the owner-approved exact audio codec options"],
        "note": "owner-specified name",
    }
    draft = drafter.plan_to_draft(item, {"examples": []}, validated)
    assert draft["build"]["function_name"] == "singing_export_codec_args"
    assert len(draft["build"]["examples"]) == 4
    assert [r["call"] for r in draft["build"]["examples"]] == VOICE_CALLS
    assert [r["expected"] for r in draft["build"]["examples"]] == VOICE_EXPECTED
    assert all(r["why"].startswith("roadmap acceptance example: ") for r in draft["build"]["examples"])
    assert any("singing_export_codec_args('wav')" in criterion for criterion in draft["acceptance_criteria"])


def test_placeholder_literal_argument_is_not_renamed() -> None:
    item = request([
        "singing_export_codec_args('{fn} literal')",
        "singing_export_codec_args('wav')",
        "singing_export_codec_args('mp3')",
    ])
    assert drafter.roadmap_examples(item)[0]["call"] == "{fn}('{fn} literal')"
    assert drafter.owner_function_name(item) == "singing_export_codec_args"
