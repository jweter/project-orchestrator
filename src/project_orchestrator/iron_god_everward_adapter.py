"""Everward Product Reality adapter (roadmap section 6: Product
Reality Engine; Phase 8: Product Reality Framework).

Declares Everward's Product Reality evidence category taxonomy from
the roadmap: nine deterministic categories -- successful Unreal
build, game startup, expected map/load state, character movement,
basic input, required assets, deterministic startup/performance
evidence, headless checks where possible, image/video evidence where
appropriate -- plus a tenth, explicitly human-debt category named
directly in the roadmap text: "Subjective visual/gameplay evaluation
may remain human debt, but deterministic game functionality should
be automated." This is the first Product Reality adapter with a
non-empty HUMAN_DEBT_CATEGORIES set (contrast with Knowledge Engine,
IG-081, and Rocksmith, IG-082, where none apply). This module is
CONTRACT ONLY, satisfying the IG-080 ProductRealityAdapter Protocol:
a real evaluation requires actually running an Unreal build, out of
scope for this session. This module performs no evaluation, file, or
network operation itself.
"""

from __future__ import annotations

from .iron_god_product_reality_adapter import ProductRealityEvidence

SUCCESSFUL_UNREAL_BUILD = "successful_unreal_build"
GAME_STARTUP = "game_startup"
EXPECTED_MAP_LOAD_STATE = "expected_map_load_state"
CHARACTER_MOVEMENT = "character_movement"
BASIC_INPUT = "basic_input"
REQUIRED_ASSETS = "required_assets"
DETERMINISTIC_STARTUP_PERFORMANCE_EVIDENCE = (
    "deterministic_startup_performance_evidence"
)
HEADLESS_CHECKS_WHERE_POSSIBLE = "headless_checks_where_possible"
IMAGE_VIDEO_EVIDENCE_WHERE_APPROPRIATE = "image_video_evidence_where_appropriate"
SUBJECTIVE_VISUAL_GAMEPLAY_EVALUATION = "subjective_visual_gameplay_evaluation"

KNOWN_CATEGORIES = (
    SUCCESSFUL_UNREAL_BUILD,
    GAME_STARTUP,
    EXPECTED_MAP_LOAD_STATE,
    CHARACTER_MOVEMENT,
    BASIC_INPUT,
    REQUIRED_ASSETS,
    DETERMINISTIC_STARTUP_PERFORMANCE_EVIDENCE,
    HEADLESS_CHECKS_WHERE_POSSIBLE,
    IMAGE_VIDEO_EVIDENCE_WHERE_APPROPRIATE,
    SUBJECTIVE_VISUAL_GAMEPLAY_EVALUATION,
)

HUMAN_DEBT_CATEGORIES: frozenset[str] = frozenset(
    {SUBJECTIVE_VISUAL_GAMEPLAY_EVALUATION}
)

_NOT_IMPLEMENTED = (
    "Everward Product Reality evaluation for {repository!r} is not "
    "implemented in this session; a real adapter requires actually running "
    "an Unreal build, which is out of scope here."
)


class EverwardAdapter:
    def evaluate(self, repository: str, candidate_sha: str) -> ProductRealityEvidence:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(repository=repository))
