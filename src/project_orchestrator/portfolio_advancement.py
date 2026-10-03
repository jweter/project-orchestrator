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
