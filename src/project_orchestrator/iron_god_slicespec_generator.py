"""SliceSpec generator (roadmap section 3.1 / Phase 6: Capability
Compiler).

Assembles the roadmap's SliceSpec schema (section 3.1's required
field list) for one capability, composing the decomposition decision
(iron_god_slice_decomposition.py, IG-063, must be DECOMPOSED) and the
ambiguity check (iron_god_ambiguity_detector.py, IG-065, must be
CLEAR). Refuses (raises) rather than generating a spec for a
capability that is not both DECOMPOSED and CLEAR -- generating a spec
in that case would recreate exactly the DIRECTION_REQUIRED condition
section 3.1 says this component exists to eliminate only when it does
NOT apply.

Fields the capability graph schema cannot supply (repository, exact
baseline SHA, allowed/forbidden paths, runner/runtime/resource
requirements, Product Reality requirements, expected artifacts, risk
class, verification policy, estimated conflict scope) are supplied by
the caller as SliceSpecInputs -- this module never invents them. This
module performs no file or state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_ambiguity_detector import CLEAR, AmbiguityCheck
from .iron_god_slice_decomposition import DECOMPOSED, SliceDecomposition


class SliceSpecGenerationError(RuntimeError):
    """Raised when asked to generate a SliceSpec for a capability that
    is not both DECOMPOSED and CLEAR, or for mismatched/invalid inputs."""


@dataclass(frozen=True)
class SliceSpecInputs:
    repository: str
    exact_baseline_sha: str
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    runner_requirements: tuple[str, ...]
    runtime_requirements: tuple[str, ...]
    resource_requirements: tuple[str, ...]
    product_reality_requirements: tuple[str, ...]
    expected_artifacts: tuple[str, ...]
    risk_class: str
    verification_policy: str
    estimated_conflict_scope: tuple[str, ...]


@dataclass(frozen=True)
class SliceSpec:
    slice_id: str
    repository: str
    objective: str
    capability_id: str
    exact_baseline_sha: str
    dependencies: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    runner_requirements: tuple[str, ...]
    runtime_requirements: tuple[str, ...]
    resource_requirements: tuple[str, ...]
    product_reality_requirements: tuple[str, ...]
    expected_artifacts: tuple[str, ...]
    risk_class: str
    verification_policy: str
    estimated_conflict_scope: tuple[str, ...]


def generate_slice_spec(
    slice_id: str,
    decomposition: SliceDecomposition,
    ambiguity_check: AmbiguityCheck,
    inputs: SliceSpecInputs,
) -> SliceSpec:
    """Assemble a SliceSpec for `decomposition.capability_id`.

    Raises SliceSpecGenerationError if `slice_id` is empty, if the
    decomposition and ambiguity check disagree on capability_id, if
    `decomposition.status != DECOMPOSED`, or if
    `ambiguity_check.status != CLEAR`.
    """
    if not isinstance(slice_id, str) or not slice_id.strip():
        raise SliceSpecGenerationError("slice_id must be a non-empty string")

    if decomposition.capability_id != ambiguity_check.capability_id:
        raise SliceSpecGenerationError(
            f"decomposition capability_id {decomposition.capability_id!r} does "
            f"not match ambiguity_check capability_id "
            f"{ambiguity_check.capability_id!r}"
        )

    if decomposition.status != DECOMPOSED:
        raise SliceSpecGenerationError(
            f"capability {decomposition.capability_id!r} decomposition status "
            f"is {decomposition.status}, not DECOMPOSED; refusing to generate "
            "a SliceSpec"
        )

    if ambiguity_check.status != CLEAR:
        raise SliceSpecGenerationError(
            f"capability {decomposition.capability_id!r} ambiguity status is "
            f"{ambiguity_check.status}, not CLEAR; refusing to generate a "
            "SliceSpec"
        )

    return SliceSpec(
        slice_id=slice_id,
        repository=inputs.repository,
        objective=decomposition.next_increment,
        capability_id=decomposition.capability_id,
        exact_baseline_sha=inputs.exact_baseline_sha,
        dependencies=decomposition.depends_on,
        acceptance_criteria=decomposition.completion_evidence,
        allowed_paths=inputs.allowed_paths,
        forbidden_paths=inputs.forbidden_paths,
        runner_requirements=inputs.runner_requirements,
        runtime_requirements=inputs.runtime_requirements,
        resource_requirements=inputs.resource_requirements,
        product_reality_requirements=inputs.product_reality_requirements,
        expected_artifacts=inputs.expected_artifacts,
        risk_class=inputs.risk_class,
        verification_policy=inputs.verification_policy,
        estimated_conflict_scope=inputs.estimated_conflict_scope,
    )
