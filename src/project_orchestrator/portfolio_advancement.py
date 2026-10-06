from __future__ import annotations

from typing import Any

STATE_ORDER = ("UNUSABLE", "FIRST_WORKING_BUILD", "USABLE_ALPHA", "USER_TESTING", "BETA", "FINISHED_TARGET")

def rank_usability_frontier(
    candidates: list[dict[str, Any]],
    usability: dict[str, Any],
    *,
    advancement_age: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Rank already-legal candidates toward usable products; never creates execution authority."""
    products = usability.get("products", {})
    ages = advancement_age or {}
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        product = candidate.get("product")
        policy = products.get(product, {}) if isinstance(products, dict) else {}
        if not isinstance(policy, dict) or not policy.get("next_gate"):
            continue
        if candidate.get("eligible") is not True:
            continue
        if candidate.get("blocked") is True or candidate.get("owned") is True:
            continue
        removes = bool(candidate.get("removes_usability_blocker"))
        direct = bool(candidate.get("advances_usability_gate"))
        try:
            age = max(int(ages.get(product, 0)), 0)
        except (TypeError, ValueError):
            age = 0
        # Lexicographic semantics: direct usability advancement dominates starvation;
        # starvation breaks ties without inventing ROI/probability/readiness percentages.
        priority = (0 if removes else 1, 0 if direct else 1, -age, str(product), str(candidate.get("slice_id") or ""))
        ranked.append({**candidate, "usability_gate": policy["next_gate"],
                       "usability_priority": priority[:3],
                       "starvation_age": age})
    ranked.sort(key=lambda row: (tuple(row["usability_priority"]), str(row.get("product")), str(row.get("slice_id") or "")))
    return ranked

def select_portfolio_frontier(
    candidates: list[dict[str, Any]], usability: dict[str, Any], *, slots: int,
    advancement_age: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Weighted round-robin: first pass gives products breadth, later passes fill remaining capacity."""
    if slots <= 0:
        return []
    ranked = rank_usability_frontier(candidates, usability, advancement_age=advancement_age)
    selected: list[dict[str, Any]] = []
    seen_products: set[str] = set()
    for row in ranked:
        product = str(row.get("product"))
        if product in seen_products:
            continue
        selected.append(row); seen_products.add(product)
        if len(selected) >= slots:
            return selected
    for row in ranked:
        if row in selected:
            continue
        selected.append(row)
        if len(selected) >= slots:
            break
    return selected


ASYNC_GATE_STATES = frozenset({
    "PREFLIGHT_RUNNING", "CI_RUNNING", "REVIEW_SETTLING",
    "PRODUCT_REALITY_RUNNING", "WINDOWS_GATE_RUNNING",
    "UNREAL_GATE_RUNNING", "OLLAMA_RUNNING", "WORKER_RUNNING",
})


def build_resilient_advancement_plan(
    candidates: list[dict[str, Any]],
    usability: dict[str, Any],
    *,
    slots: int,
    advancement_age: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Plan productive lanes while rotating around asynchronous or route-local stalls.

    This function only ranks candidates that already carry execution authority.
    It never makes blocked/ineligible work legal and never changes repository gates.
    """
    if slots <= 0:
        return {"selected": [], "waiting": [], "route_blocked": [], "capacity": 0}

    ready: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    route_blocked: list[dict[str, Any]] = []
    for candidate in candidates:
        gate_state = str(candidate.get("gate_state") or "")
        if gate_state in ASYNC_GATE_STATES:
            waiting.append({**candidate, "rotation_reason": "ASYNC_GATE_IN_FLIGHT"})
            continue
        routes = candidate.get("authorized_mutation_routes")
        failed_routes = frozenset(candidate.get("failed_mutation_routes") or ())
        if isinstance(routes, (list, tuple)) and routes:
            available = [route for route in routes if route not in failed_routes]
            if not available:
                route_blocked.append({**candidate, "rotation_reason": "AUTHORIZED_ROUTES_EXHAUSTED"})
                continue
            candidate = {**candidate, "mutation_route": available[0]}
        ready.append(candidate)

    selected = select_portfolio_frontier(
        ready, usability, slots=slots, advancement_age=advancement_age
    )
    return {
        "selected": selected,
        "waiting": waiting,
        "route_blocked": route_blocked,
        "capacity": slots,
    }
