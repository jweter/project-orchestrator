from __future__ import annotations

import argparse
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
STATE_KEYS = {
    "run_id",
    "decision_id",
    "priority_class",
    "project_id",
    "current_gate",
    "blocked_reason",
    "worker_result_class",
    "product_reality_state",
}
CANDIDATE_KEYS = {
    "candidate_id",
    "project_id",
    "repository",
    "title",
    "priority",
    "eligibility_state",
    "verification_burden",
    "resource_class",
    "continuation_kind",
}


class JevShadowError(ValueError):
    pass


@dataclass(frozen=True)
class ShadowDecision:
    state: str
    selected_candidate_id: str | None
    probabilities: dict[str, float]
    reason: str
    neural_call_made: bool
    execution_authority: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "selected_candidate_id": self.selected_candidate_id,
            "probabilities": self.probabilities,
            "reason": self.reason,
            "neural_call_made": self.neural_call_made,
            "execution_authority": self.execution_authority,
        }


def _sanitize_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise JevShadowError("shadow state values must be JSON scalars")


def sanitize_state(state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise JevShadowError("state must be an object")
    return {
        key: _sanitize_scalar(state[key])
        for key in sorted(STATE_KEYS)
        if key in state
    }


def legal_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        raise JevShadowError("candidates must be a list")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in candidates:
        if not isinstance(raw, dict):
            raise JevShadowError("each candidate must be an object")
        if raw.get("eligibility_state") != "ELIGIBLE":
            continue
        candidate_id = raw.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise JevShadowError("eligible candidate requires candidate_id")
        if candidate_id in seen:
            raise JevShadowError(f"duplicate candidate_id: {candidate_id}")
        seen.add(candidate_id)
        clean = {key: raw[key] for key in sorted(CANDIDATE_KEYS) if key in raw}
        for required in ("project_id", "repository", "title"):
            if not isinstance(clean.get(required), str) or not clean[required]:
                raise JevShadowError(f"{candidate_id}: missing {required}")
        out.append(clean)
    return out


def build_shadow_request(
    *,
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    legal = legal_candidates(candidates)
    if len(legal) < 2:
        raise JevShadowError("neural decision requires at least two legal candidates")
    criteria = {
        candidate["candidate_id"]: {
            key: candidate[key]
            for key in (
                "project_id",
                "repository",
                "title",
                "priority",
                "verification_burden",
                "resource_class",
                "continuation_kind",
            )
            if key in candidate
        }
        for candidate in legal
    }
    return {
        "state": {
            **sanitize_state(state),
            "legal_candidates": criteria,
            "authority_notice": (
                "All options were admitted by deterministic policy before this request. "
                "Choose only among these options. This answer is advisory shadow evidence "
                "and cannot authorize execution, merge, gate bypass, or Product Reality."
            ),
        },
        "model": model,
        "questions": {
            "next_candidate": {
                "type": "choice",
                "instructions": (
                    "Which already-legal candidate should be attempted next to maximize "
                    "verified product advancement while avoiding false idle and unnecessary "
                    "verification churn? Prefer existing failing/incomplete work over new work "
                    "when deterministic priority is otherwise equal."
                ),
                "criteria": criteria,
            }
        },
    }


def _probabilities(answer: dict[str, Any], legal_ids: set[str]) -> dict[str, float]:
    raw = answer.get("probabilities", {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        if key not in legal_ids:
            continue
        if isinstance(value, (int, float)) and 0.0 <= float(value) <= 1.0:
            out[str(key)] = float(value)
    return out


def parse_shadow_response(
    response: dict[str, Any],
    *,
    candidates: list[dict[str, Any]],
) -> ShadowDecision:
    legal = legal_candidates(candidates)
    legal_ids = {item["candidate_id"] for item in legal}
    answers = response.get("answers")
    if not isinstance(answers, dict):
        answers = response.get("choices")
    if not isinstance(answers, dict):
        return ShadowDecision("INVALID_RESPONSE", None, {}, "missing answer mapping", True)
    answer = answers.get("next_candidate")
    if not isinstance(answer, dict):
        return ShadowDecision("INVALID_RESPONSE", None, {}, "missing next_candidate answer", True)
    selected = answer.get("choice")
    if selected not in legal_ids:
        return ShadowDecision(
            "OUT_OF_SET",
            None,
            _probabilities(answer, legal_ids),
            "provider selected a candidate outside deterministic legal set",
            True,
        )
    return ShadowDecision(
        "SHADOW_RECOMMENDATION",
        str(selected),
        _probabilities(answer, legal_ids),
        "valid advisory recommendation; deterministic scheduler remains authoritative",
        True,
    )


def decide_without_network(
    *,
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> ShadowDecision:
    del state
    legal = legal_candidates(candidates)
    if not legal:
        return ShadowDecision(
            "NO_LEGAL_CANDIDATE",
            None,
            {},
            "deterministic eligibility produced no legal candidate; Jev is not called",
            False,
        )
    if len(legal) == 1:
        return ShadowDecision(
            "DETERMINISTIC_SINGLETON",
            legal[0]["candidate_id"],
            {legal[0]["candidate_id"]: 1.0},
            "only one legal candidate exists; neural inference is unnecessary",
            False,
        )
    return ShadowDecision(
        "SHADOW_PENDING",
        None,
        {},
        "multiple legal candidates exist; shadow evaluation may be requested",
        False,
    )


def _http_post(payload: dict[str, Any], *, endpoint: str, api_key: str, timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "project-orchestrator-jev-shadow/1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise JevShadowError("provider response must be a JSON object")
    return data


def evaluate_shadow(
    *,
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
    live: bool = False,
    model: str = DEFAULT_MODEL,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: float = 15.0,
    transport: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    local = decide_without_network(state=state, candidates=candidates)
    legal = legal_candidates(candidates)
    result: dict[str, Any] = {
        "schema_version": 1,
        "mode": "SHADOW_ONLY",
        "deterministic_legal_candidate_ids": [item["candidate_id"] for item in legal],
        "decision": local.as_dict(),
        "safety_boundaries": [
            "deterministic policy defines the legal action space before Jev",
            "Jev has no execution, merge, promotion, Product Reality, security, privacy, licensing, or provenance authority",
            "provider output outside the legal candidate set fails closed",
            "one legal candidate skips neural inference",
            "credentials are environment-only and are never serialized into telemetry",
            "shadow recommendations never satisfy a verification or promotion gate",
        ],
    }
    if local.state != "SHADOW_PENDING":
        return result
    if not live:
        result["request"] = build_shadow_request(state=state, candidates=candidates, model=model)
        return result
    if os.environ.get("JEV_SHADOW_LIVE") != "1":
        result["decision"] = ShadowDecision(
            "LIVE_DISABLED",
            None,
            {},
            "live Jev calls require explicit JEV_SHADOW_LIVE=1",
            False,
        ).as_dict()
        return result
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        result["decision"] = ShadowDecision(
            "CREDENTIAL_MISSING",
            None,
            {},
            "TYPESAFE_API_KEY is not configured",
            False,
        ).as_dict()
        return result
    payload = build_shadow_request(state=state, candidates=candidates, model=model)
    sender = transport or _http_post
    response = sender(payload, endpoint=endpoint, api_key=api_key, timeout=timeout)
    result["decision"] = parse_shadow_response(response, candidates=candidates).as_dict()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate already-legal orchestration candidates with Jev in shadow mode.")
    parser.add_argument("input", type=Path, help="JSON with state and candidates")
    parser.add_argument("--live", action="store_true", help="call TypeSafe only when JEV_SHADOW_LIVE=1 and TYPESAFE_API_KEY are also set")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    result = evaluate_shadow(
        state=data.get("state", {}),
        candidates=data.get("candidates", []),
        live=args.live,
        model=args.model,
        endpoint=args.endpoint,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
