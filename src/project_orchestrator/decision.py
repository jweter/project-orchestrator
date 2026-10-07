from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5, "P6": 6}
MERGEABLE_REQUIRED_CHECK_STATES = {"RESOLVED", "CONFIRMED_NONE"}


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
    if pr.get("blocking_comments_state") == "UNKNOWN":
        return "UNCERTAIN"
    if pr.get("blocking_reviews", 0):
        return "BLOCKED"

    check_state = pr.get("required_checks_state")
    if check_state not in MERGEABLE_REQUIRED_CHECK_STATES:
        return "UNCERTAIN"

    checks = pr.get("required_checks")
    if check_state == "CONFIRMED_NONE":
        checks = []
    elif not isinstance(checks, list) or not checks:
        return "UNCERTAIN"

    conclusions = {item.get("conclusion") for item in checks}
    statuses = {item.get("status") for item in checks}
    if "missing" in statuses:
        return "UNCERTAIN"
    if conclusions & {"failure", "cancelled", "timed_out"}:
        return "FAILED"
    if any(status != "completed" for status in statuses):
        return "PENDING"
    if any(conclusion != "success" for conclusion in conclusions):
        return "UNCERTAIN"
    if not pr.get("mergeable", False):
        return "UNCERTAIN"
    if pr.get("blocking_comments", 0):
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


def pr_accountability(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one deterministic disposition for every open PR in the portfolio.

    This is the CAPA enforcement surface: an open PR may never disappear behind
    priority filtering or a scheduler report.  Each PR must be explicitly owned by
    repair, merge, resolution, or pending-CI handling on every fresh snapshot.
    """
    dispositions: list[dict[str, Any]] = []
    for project in snapshot.get("projects", []):
        for repo in project.get("repositories", []):
            for pr in repo.get("open_prs", []):
                state = classify_pr(pr)
                action = {
                    "FAILED": "repair_pr",
                    "GREEN": "merge_pr",
                    "CONFLICTED": "resolve_pr",
                    "BLOCKED": "resolve_pr",
                    "UNCERTAIN": "resolve_pr",
                    "PENDING": "wait_pr",
                }[state]
                dispositions.append({
                    "project": project["id"],
                    "repository": repo["name"],
                    "number": pr.get("number"),
                    "head_sha": pr.get("head_sha"),
                    "state": state,
                    "required_action": action,
                })
    return dispositions


def assert_pr_accountability(snapshot: dict[str, Any], handled: list[dict[str, Any]]) -> None:
    """Fail a cycle that did not account for every exact-head open PR disposition."""
    expected = {(d["repository"], d["number"], d["head_sha"], d["required_action"]) for d in pr_accountability(snapshot)}
    observed = {(d.get("repository"), d.get("number"), d.get("head_sha"), d.get("required_action")) for d in handled}
    missing = expected - observed
    if missing:
        raise RuntimeError(f"P1 PR_ACCOUNTABILITY_DEFECT: {sorted(missing)!r}")


def next_actions(snapshot: dict[str, Any]) -> list[Action]:
    actions = actions_from_snapshot(snapshot)
    if not actions:
        return []
    highest = min(PRIORITY[action.priority] for action in actions)
    candidates = [action for action in actions if PRIORITY[action.priority] == highest]
    if highest < PRIORITY["P5"]:
        # P0-P3 are ordered first, but PR obligations are work-conserving: a
        # failed PR must never hide a simultaneously green exact-head merge,
        # nor may either disappear behind a conflict/review resolution lane.
        pr_actions = [a for a in actions if a.kind in {"repair_pr", "merge_pr", "resolve_pr"}]
        return pr_actions or candidates

    selected: list[Action] = []
    seen_projects: set[str] = set()
    for action in candidates:
        if action.project not in seen_projects:
            selected.append(action)
            seen_projects.add(action.project)
    return selected
