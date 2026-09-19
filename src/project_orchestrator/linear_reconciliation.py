"""Deterministic GitHub -> Linear state reconciliation policy."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class GithubWorkState:
    issue_open: bool = True
    implementation_active: bool = False
    open_prs: int = 0
    merged_prs: int = 0
    blocked: bool = False
    human_required: bool = False

@dataclass(frozen=True)
class LinearDecision:
    state: str
    add_labels: tuple[str, ...] = ()
    remove_labels: tuple[str, ...] = ()

_MANAGED = ("Blocked", "Human Required")

def reconcile(state: GithubWorkState) -> LinearDecision:
    labels=[]
    if state.blocked: labels.append("Blocked")
    if state.human_required: labels.append("Human Required")
    if not state.issue_open or state.merged_prs > 0: target="Done"
    elif state.open_prs > 0: target="In Review"
    elif state.implementation_active: target="In Progress"
    else: target="Todo"
    return LinearDecision(target, tuple(labels), tuple(x for x in _MANAGED if x not in labels))

def extract_github_refs(description: str) -> tuple[str, int] | None:
    """Parse the authoritative Phase-1 marker: owner/repo#123."""
    marker="**Authoritative source:** "
    for line in (description or "").splitlines():
        if line.startswith(marker):
            ref=line[len(marker):].strip()
            repo, sep, number=ref.rpartition("#")
            if sep and repo.count("/")==1 and number.isdigit():
                return repo, int(number)
    return None

def pr_references_issue(pr: dict, issue_number: int) -> bool:
    """Conservative textual linkage; linkage alone never proves merge."""
    import re
    value="\n".join(str(pr.get(k) or "") for k in ("title","body"))
    return bool(re.search(rf"(?i)(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)?\s*#?{issue_number}\b", value))


def choose_queue_refills(items: list[dict]) -> list[str]:
    """Select one executable backlog item per product with no active queue item."""
    by_project: dict[str,list[dict]] = {}
    for item in items:
        project=item.get("project")
        if project and project != "Exponential Engine / Project Orchestrator":
            by_project.setdefault(project,[]).append(item)
    selected=[]
    for project, rows in by_project.items():
        active=any(r.get("status") in {"Todo","In Progress","In Review"} for r in rows)
        if active: continue
        candidates=[r for r in rows if r.get("status")=="Backlog" and "Human Required" not in r.get("labels",[]) and "Product Reality" not in r.get("labels",[])]
        if candidates:
            candidates.sort(key=lambda r:(r.get("priority_rank",99),r.get("linear_id","")))
            selected.append(candidates[0]["linear_id"])
    return selected
