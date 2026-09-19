"""Evidence-gated skill refinement inspired by Hermes procedural learning."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

class SkillLifecycle(str, Enum):
    UNMEASURED="UNMEASURED"; PILOT="PILOT"; PROVEN="PROVEN"; DEGRADED="DEGRADED"

@dataclass(frozen=True)
class SkillObservation:
    skill_id: str
    run_id: str
    applied: bool
    verification_passed: bool
    prevented_known_failure: bool = False

@dataclass(frozen=True)
class SkillEffectiveness:
    skill_id: str
    applications: int
    verified_successes: int
    prevented_failures: int
    success_rate: float
    state: SkillLifecycle

def evaluate_skill(skill_id: str, observations: Iterable[SkillObservation], *, min_proven_samples: int=3, proven_rate: float=0.8) -> SkillEffectiveness:
    if min_proven_samples < 1: raise ValueError("min_proven_samples must be positive")
    if not 0.0 <= proven_rate <= 1.0: raise ValueError("proven_rate must be between 0 and 1")
    seen:set[str]=set(); applied=[]
    for obs in observations:
        if obs.skill_id != skill_id or not obs.applied: continue
        if obs.run_id in seen: raise ValueError("duplicate run observation")
        seen.add(obs.run_id); applied.append(obs)
    count=len(applied); successes=sum(x.verification_passed for x in applied)
    prevented=sum(x.verification_passed and x.prevented_known_failure for x in applied)
    rate=(successes/count) if count else 0.0
    if count == 0: state=SkillLifecycle.UNMEASURED
    elif count >= min_proven_samples and rate >= proven_rate: state=SkillLifecycle.PROVEN
    elif count >= min_proven_samples and rate < 0.5: state=SkillLifecycle.DEGRADED
    else: state=SkillLifecycle.PILOT
    return SkillEffectiveness(skill_id,count,successes,prevented,round(rate,4),state)

def should_inject(effectiveness: SkillEffectiveness) -> bool:
    return effectiveness.state in {SkillLifecycle.PILOT, SkillLifecycle.PROVEN}

def refinement_action(effectiveness: SkillEffectiveness) -> str:
    if effectiveness.state is SkillLifecycle.UNMEASURED: return "COLLECT_EVIDENCE"
    if effectiveness.state is SkillLifecycle.PILOT: return "CONTINUE_PILOT"
    if effectiveness.state is SkillLifecycle.PROVEN: return "REUSE"
    return "REVIEW_OR_RETIRE"
