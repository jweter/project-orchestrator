from __future__ import annotations

from typing import Any

from .retained_learning import applicable_rules


def apply_retained_policy(
    store: dict[str, Any],
    work_item: dict[str, Any],
) -> dict[str, Any]:
    repository = work_item.get("repository")
    changed_paths = work_item.get("changed_paths", [])
    if not isinstance(repository, str) or not repository:
        raise ValueError("work item requires repository")
    if not isinstance(changed_paths, list) or not all(isinstance(x, str) for x in changed_paths):
        raise ValueError("changed_paths must be a list of strings")
    rules = applicable_rules(
        store,
        repository=repository,
        changed_paths=changed_paths,
        failure_class=work_item.get("failure_class"),
    )
    return {
        **work_item,
        "retained_policy": {
            "applied_rule_ids": [rule["rule_id"] for rule in rules],
            "mechanisms": [rule["mechanism"] for rule in rules],
            "evidence_refs": sorted({ref for rule in rules for ref in rule.get("evidence_refs", [])}),
            "authority": "ADVISORY_ONLY_NORMAL_GATES_REQUIRED",
        },
    }


def scheduler_context(store: dict[str, Any], work_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [apply_retained_policy(store, item) for item in work_items]
