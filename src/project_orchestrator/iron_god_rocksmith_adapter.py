"""Rocksmith Product Reality adapter (roadmap section 6: Product
Reality Engine; Phase 8: Product Reality Framework).

Declares the Rocksmith CDLC Generator's Product Reality evidence
category taxonomy from the roadmap -- real CDLC generation, valid
package structure, real arrangement generation, timing validation,
Lead/Rhythm/Bass preservation, note/chord metadata validation,
song-specific regression checks, successful parser/import
verification, playable or tool-verifiable output where available --
none of which the roadmap marks as inherently subjective/human-only
for this product. Per the roadmap: "The system must not call a
Rocksmith milestone complete simply because a Python function
returned successfully." This module is CONTRACT ONLY, satisfying the
IG-080 ProductRealityAdapter Protocol: a real evaluation requires
actually running Rocksmith CDLC generation/parsing, out of scope for
this session. This module performs no evaluation, file, or network
operation itself.
"""

from __future__ import annotations

from .iron_god_product_reality_adapter import ProductRealityEvidence

REAL_CDLC_GENERATION = "real_cdlc_generation"
VALID_PACKAGE_STRUCTURE = "valid_package_structure"
REAL_ARRANGEMENT_GENERATION = "real_arrangement_generation"
TIMING_VALIDATION = "timing_validation"
LEAD_RHYTHM_BASS_PRESERVATION = "lead_rhythm_bass_preservation"
NOTE_CHORD_METADATA_VALIDATION = "note_chord_metadata_validation"
SONG_SPECIFIC_REGRESSION_CHECKS = "song_specific_regression_checks"
SUCCESSFUL_PARSER_IMPORT_VERIFICATION = "successful_parser_import_verification"
PLAYABLE_OR_TOOL_VERIFIABLE_OUTPUT = "playable_or_tool_verifiable_output"

KNOWN_CATEGORIES = (
    REAL_CDLC_GENERATION,
    VALID_PACKAGE_STRUCTURE,
    REAL_ARRANGEMENT_GENERATION,
    TIMING_VALIDATION,
    LEAD_RHYTHM_BASS_PRESERVATION,
    NOTE_CHORD_METADATA_VALIDATION,
    SONG_SPECIFIC_REGRESSION_CHECKS,
    SUCCESSFUL_PARSER_IMPORT_VERIFICATION,
    PLAYABLE_OR_TOOL_VERIFIABLE_OUTPUT,
)

HUMAN_DEBT_CATEGORIES: frozenset[str] = frozenset()

_NOT_IMPLEMENTED = (
    "Rocksmith Product Reality evaluation for {repository!r} is not "
    "implemented in this session; a real adapter requires actually running "
    "Rocksmith CDLC generation/parsing, which is out of scope here."
)


class RocksmithAdapter:
    def evaluate(self, repository: str, candidate_sha: str) -> ProductRealityEvidence:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(repository=repository))
