from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5, "P6": 6}


@dataclass(frozen=True)
class Action:
    priority: str
    project: str
    repository: str
    kind: str
    ref: str | None
    reason: str

    @property
    def sort_key(self) -> tuple[int, str, str, str]:
        return (PRIORITY[self.priority], self.project, self.repository, self.ref or "")


def classify_pr(pr: dict[str, Any]) -> str:
    if pr.get("conflicted"):
        return "CONFLICTED"
    if pr.get("blocked"):
        return "BLOCKED"
    checks = pr.get("required_checks")
    if not isinstance(checks, list) or not checks:
        return "UNCERTAIN"
    conclusions = {item.get("conclusion") for item in checks}
    statuses = {item.get("status") for item in checks}
    if conclusions & {"failure", "cancelled", "timed_out"}:
        return "FAILED"
    if any(status != "completed" for status in statuses):
        return "PENDING"
    if any(conclusion != "success" for conclusion in conclusions):
        return "UNCERTAIN"
    if not pr.get("mergeable", False):
        return "UNCERTAIN"
    if pr.get("blocking_reviews", 0) or pr.get("blocking_comments", 0):
        return "BLOCKED"
    return "GREEN"


def actions_from_snapshot(snapshot: dict[str, Any]) -> list[Action]:
    actions: list[Action] = []
    for project in snapshot.get("projects", []):
        project_id = project["id"]
        for repo in project.get("repositories", []):
            repo_name = repo["name"]
            for pr in repo.get("open_prs", []):
                state = classify_pr(pr)
                number = pr.get("number")
                ref = f"PR #{number}" if number is not None else "PR"
                if state == "FAILED":
                    actions.append(Action("P1", project_id, repo_name, "repair_pr", ref, "required CI failed"))
                elif state == "GREEN":
                    actions.append(Action("P2", project_id, repo_name, "merge_pr", ref, "all supplied required gates are green"))
                elif state in {"CONFLICTED", "BLOCKED", "UNCERTAIN"}:
                    actions.append(Action("P3", project_id, repo_name, "resolve_pr", ref, state.lower()))
                elif state == "PENDING":
                    actions.append(Action("P4", project_id, repo_name, "wait_pr", ref, "required CI pending"))

            for item in repo.get("started_work", []):
                actions.append(Action("P4", project_id, repo_name, "finish_started_work", item.get("ref"), item.get("reason", "started work")))

            if repo.get("new_roadmap_slice"):
                actions.append(Action("P5", project_id, repo_name, "new_roadmap_slice", None, str(repo["new_roadmap_slice"])))

    return sorted(actions, key=lambda action: action.sort_key)


def next_actions(snapshot: dict[str, Any]) -> list[Action]:
    actions = actions_from_snapshot(snapshot)
    if not actions:
        return []
    highest = min(PRIORITY[action.priority] for action in actions)
    candidates = [action for action in actions if PRIORITY[action.priority] == highest]
    if highest < PRIORITY["P5"]:
        return candidates

    selected: list[Action] = []
    seen_projects: set[str] = set()
    for action in candidates:
        if action.project not in seen_projects:
            selected.append(action)
            seen_projects.add(action.project)
    return selected
