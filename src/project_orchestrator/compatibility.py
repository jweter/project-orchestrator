from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CompatibilityError(ValueError):
    """Raised when a Knowledge Engine compatibility request is invalid."""


@dataclass(frozen=True)
class CompatibilityAction:
    order: int
    repository_role: str
    action: str
    reason: str


def load_contract(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_contract(payload)
    return payload


def validate_contract(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 1:
        raise CompatibilityError("compatibility schema_version must be 1")
    if payload.get("project") != "knowledge-engine":
        raise CompatibilityError("compatibility contract project must be knowledge-engine")

    roles = payload.get("roles")
    if not isinstance(roles, dict) or set(roles) != {"core", "ai", "web"}:
        raise CompatibilityError("roles must be exactly core, ai, and web")

    edges = payload.get("edges")
    if not isinstance(edges, list) or not edges:
        raise CompatibilityError("compatibility edges must be a non-empty list")

    seen: set[tuple[str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise CompatibilityError("every edge must be an object")
        producer = edge.get("producer")
        consumer = edge.get("consumer")
        if producer not in roles or consumer not in roles or producer == consumer:
            raise CompatibilityError("edge producer/consumer roles are invalid")
        pair = (producer, consumer)
        if pair in seen:
            raise CompatibilityError(f"duplicate compatibility edge: {producer}->{consumer}")
        seen.add(pair)
        kinds = edge.get("contract_kinds")
        if not isinstance(kinds, list) or not kinds or not all(
            isinstance(kind, str) and kind for kind in kinds
        ):
            raise CompatibilityError(f"{producer}->{consumer} requires contract_kinds")

    forbidden = payload.get("forbidden_boundaries")
    if not isinstance(forbidden, list):
        raise CompatibilityError("forbidden_boundaries must be a list")

    review_kinds = payload.get("change_kinds_requiring_review")
    if not isinstance(review_kinds, list) or not review_kinds:
        raise CompatibilityError("change_kinds_requiring_review must be non-empty")


def _direct_edges(
    contract: dict[str, Any], producer: str, kinds: set[str]
) -> list[dict[str, Any]]:
    return [
        edge
        for edge in contract["edges"]
        if edge["producer"] == producer
        and kinds.intersection(edge["contract_kinds"])
    ]


def _transitive_consumers(contract: dict[str, Any], roots: set[str]) -> set[str]:
    discovered = set(roots)
    frontier = list(roots)
    while frontier:
        producer = frontier.pop()
        for edge in contract["edges"]:
            if edge["producer"] == producer and edge["consumer"] not in discovered:
                discovered.add(edge["consumer"])
                frontier.append(edge["consumer"])
    return discovered - roots


def plan_change(change: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    validate_contract(contract)
    producer = change.get("producer_role")
    if producer not in contract["roles"]:
        raise CompatibilityError("producer_role must be core, ai, or web")

    kinds = change.get("contract_kinds")
    if not isinstance(kinds, list) or not kinds or not all(
        isinstance(kind, str) and kind for kind in kinds
    ):
        raise CompatibilityError("contract_kinds must be a non-empty string list")

    unique_kinds = set(kinds)
    known_kinds = set(contract["change_kinds_requiring_review"])
    unknown = sorted(unique_kinds - known_kinds)
    if unknown:
        raise CompatibilityError(f"unknown contract_kinds: {unknown}")

    breaking = change.get("breaking")
    if not isinstance(breaking, bool):
        raise CompatibilityError("breaking must be true or false")

    direct_edges = _direct_edges(contract, producer, unique_kinds)
    direct_consumers = {edge["consumer"] for edge in direct_edges}
    transitive_consumers = _transitive_consumers(contract, direct_consumers)
    all_consumers = direct_consumers | transitive_consumers

    actions: list[CompatibilityAction] = []
    order = 10

    for edge in sorted(direct_edges, key=lambda item: item["consumer"]):
        actions.append(
            CompatibilityAction(
                order,
                edge["consumer"],
                "verify_direct_consumer",
                (
                    f"Verify {edge['boundary']} against the proposed "
                    f"{producer} contract change before producer merge."
                ),
            )
        )
        order += 10

    for consumer in sorted(transitive_consumers - direct_consumers):
        actions.append(
            CompatibilityAction(
                order,
                consumer,
                "verify_transitive_consumer",
                f"Verify downstream behavior because {consumer} depends on a directly affected consumer.",
            )
        )
        order += 10

    if breaking:
        for consumer in sorted(all_consumers):
            actions.append(
                CompatibilityAction(
                    order,
                    consumer,
                    "prepare_compatibility",
                    "Prepare dependent compatibility before the producer change is allowed to land.",
                )
            )
            order += 10
        actions.append(
            CompatibilityAction(
                order,
                producer,
                "preserve_temporary_compatibility",
                "Preserve old/new contract compatibility when practical to avoid a broken family between merges.",
            )
        )
        order += 10

    actions.append(
        CompatibilityAction(
            order,
            producer,
            "producer_verification",
            "Run the producer's exact-head preflight and fresh required PR CI.",
        )
    )
    order += 10

    actions.append(
        CompatibilityAction(
            order,
            producer,
            "merge_producer_dependency_safe",
            (
                "Merge only after affected consumers are prepared/verified and "
                "all producer merge gates are GREEN."
            ),
        )
    )

    state = "REVIEW_REQUIRED" if direct_edges else "NO_CROSS_REPO_EDGE"
    if breaking and direct_edges:
        state = "COORDINATED_BREAKING_CHANGE"

    return {
        "schema_version": 1,
        "project": "knowledge-engine",
        "producer_role": producer,
        "contract_kinds": sorted(unique_kinds),
        "breaking": breaking,
        "compatibility_state": state,
        "direct_consumers": sorted(direct_consumers),
        "transitive_consumers": sorted(transitive_consumers - direct_consumers),
        "affected_repositories": [
            contract["roles"][role] for role in sorted({producer, *all_consumers})
        ],
        "actions": [action.__dict__ for action in actions],
        "merge_rule": (
            contract["breaking_change_rule"]
            if breaking
            else contract["additive_change_rule"]
        ),
    }


def validate_boundary(
    source_role: str, target_role: str, kind: str, contract: dict[str, Any]
) -> dict[str, Any]:
    validate_contract(contract)
    for boundary in contract["forbidden_boundaries"]:
        if (
            boundary.get("source") == source_role
            and boundary.get("target") == target_role
            and boundary.get("kind") == kind
        ):
            return {
                "allowed": False,
                "reason": boundary["reason"],
                "source_role": source_role,
                "target_role": target_role,
                "kind": kind,
            }
    return {
        "allowed": True,
        "reason": "No forbidden boundary rule matched; repository governing documents remain authoritative.",
        "source_role": source_role,
        "target_role": target_role,
        "kind": kind,
    }
