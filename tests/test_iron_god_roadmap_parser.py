from __future__ import annotations

import pytest

from project_orchestrator.iron_god_roadmap_parser import (
    RoadmapParseError,
    parse_roadmap,
)

SEP = "⸻"
EM_DASH = "—"

FAITHFUL_EXCERPT = f"""
28. Project Roadmap

Phase 0 {EM_DASH} Architecture Freeze and Safety Contract

Deliverables

IG-001 {EM_DASH} Document authority hierarchy
IG-002 {EM_DASH} Define SliceSpec schema

Exit criteria

No coding begins until every major state transition and authority boundary is documented.

{SEP}

Phase 9 {EM_DASH} Publication Independence Stress Tests

Scenarios

1. GitHub unavailable for one hour.
2. PR creation denied.

Exit criteria

Independent development continues whenever safety permits.

No verified commit is lost.

{SEP}

Phase 10 {EM_DASH} Controlled Migration

Do not replace the current engine all at once.

Stage A {EM_DASH} Shadow Mode

New Iron God state engine observes current work.

{SEP}
"""


def test_parses_real_document_shape_with_deliverables_and_exit_criteria():
    result = parse_roadmap(FAITHFUL_EXCERPT)
    phase_zero = next(p for p in result.phases if p.phase_number == 0)

    assert phase_zero.title == "Architecture Freeze and Safety Contract"
    assert phase_zero.deliverables == (
        ("IG-001", "Document authority hierarchy"),
        ("IG-002", "Define SliceSpec schema"),
    )
    assert phase_zero.exit_criteria == (
        "No coding begins until every major state transition and authority "
        "boundary is documented."
    )


def test_phase_with_no_deliverables_header_has_empty_deliverables_no_error():
    result = parse_roadmap(FAITHFUL_EXCERPT)
    phase_nine = next(p for p in result.phases if p.phase_number == 9)

    assert phase_nine.deliverables == ()
    assert phase_nine.exit_criteria == (
        "Independent development continues whenever safety permits. "
        "No verified commit is lost."
    )


def test_phase_with_no_exit_criteria_section_is_none_no_error():
    result = parse_roadmap(FAITHFUL_EXCERPT)
    phase_ten = next(p for p in result.phases if p.phase_number == 10)

    assert phase_ten.exit_criteria is None
    assert phase_ten.deliverables == ()


def test_non_phase_blocks_are_skipped():
    result = parse_roadmap(FAITHFUL_EXCERPT)
    assert {p.phase_number for p in result.phases} == {0, 9, 10}


def test_deliverables_header_with_no_recognized_lines_raises():
    text = f"""
Phase 1 {EM_DASH} Broken Phase

Deliverables

nothing here matches the expected pattern

Exit criteria

some criteria

{SEP}
"""
    with pytest.raises(RoadmapParseError):
        parse_roadmap(text)


def test_duplicate_deliverable_id_across_phases_raises():
    text = f"""
Phase 1 {EM_DASH} First

Deliverables

IG-001 {EM_DASH} first description

Exit criteria

criteria one

{SEP}

Phase 2 {EM_DASH} Second

Deliverables

IG-001 {EM_DASH} duplicated description

Exit criteria

criteria two

{SEP}
"""
    with pytest.raises(RoadmapParseError):
        parse_roadmap(text)


def test_duplicate_phase_number_raises():
    text = f"""
Phase 1 {EM_DASH} First

Exit criteria

criteria one

{SEP}

Phase 1 {EM_DASH} First Again

Exit criteria

criteria two

{SEP}
"""
    with pytest.raises(RoadmapParseError):
        parse_roadmap(text)


def test_empty_document_yields_no_phases():
    result = parse_roadmap("")
    assert result.phases == ()
