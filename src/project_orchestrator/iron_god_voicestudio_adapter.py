"""VoiceStudio Product Reality adapter (roadmap section 6: Product
Reality Engine; Phase 8: Product Reality Framework).

Declares VoiceStudio's Product Reality evidence category taxonomy
from the roadmap -- real audio input, deterministic pitch/F0
extraction, valid melody representation, synthesis pipeline
completion, output audio artifact, correct metadata/schema,
representative golden examples, usable output rather than merely
successful serialization -- none of which the roadmap marks as
inherently subjective/human-only for this product. This module is
CONTRACT ONLY, satisfying the IG-080 ProductRealityAdapter Protocol:
a real evaluation requires actually running the audio DSP/synthesis
pipeline, out of scope for this session. This module performs no
evaluation, file, or network operation itself.
"""

from __future__ import annotations

from .iron_god_product_reality_adapter import ProductRealityEvidence

REAL_AUDIO_INPUT = "real_audio_input"
DETERMINISTIC_PITCH_F0_EXTRACTION = "deterministic_pitch_f0_extraction"
VALID_MELODY_REPRESENTATION = "valid_melody_representation"
SYNTHESIS_PIPELINE_COMPLETION = "synthesis_pipeline_completion"
OUTPUT_AUDIO_ARTIFACT = "output_audio_artifact"
CORRECT_METADATA_SCHEMA = "correct_metadata_schema"
REPRESENTATIVE_GOLDEN_EXAMPLES = "representative_golden_examples"
USABLE_OUTPUT_NOT_MERE_SERIALIZATION = "usable_output_not_mere_serialization"

KNOWN_CATEGORIES = (
    REAL_AUDIO_INPUT,
    DETERMINISTIC_PITCH_F0_EXTRACTION,
    VALID_MELODY_REPRESENTATION,
    SYNTHESIS_PIPELINE_COMPLETION,
    OUTPUT_AUDIO_ARTIFACT,
    CORRECT_METADATA_SCHEMA,
    REPRESENTATIVE_GOLDEN_EXAMPLES,
    USABLE_OUTPUT_NOT_MERE_SERIALIZATION,
)

HUMAN_DEBT_CATEGORIES: frozenset[str] = frozenset()

_NOT_IMPLEMENTED = (
    "VoiceStudio Product Reality evaluation for {repository!r} is not "
    "implemented in this session; a real adapter requires actually running "
    "the audio DSP/synthesis pipeline, which is out of scope here."
)


class VoiceStudioAdapter:
    def evaluate(self, repository: str, candidate_sha: str) -> ProductRealityEvidence:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(repository=repository))
