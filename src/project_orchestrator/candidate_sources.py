from __future__ import annotations

from typing import Any

PROJECT_BY_REPOSITORY = {
    "jweter/knowledge-engine-core": "knowledge-engine",
    "jweter/knowledge-engine-web": "knowledge-engine",
    "jweter/knowledge-engine-ai": "knowledge-engine",
    "jweter/rocksmith-cdlc-generator": "rocksmith",
    "jweter/Project-Everward": "everward",
}


class CandidateSourceError(ValueError):
    pass


def _project(repository: str) -> str:
    try:
        return PROJECT_BY_REPOSITORY[repository]
    except KeyError as exc:
        raise CandidateSourceError(f"repository is outside product portfolio: {repository}") from exc


def _priority(value: Any, default: str) -> str:
    result = str(value or default).upper()
    if result not in {f"P{i}" for i in range(7)}:
        raise CandidateSourceError("priority must be P0..P6")
    return result


def issue_candidate(issue: dict[str, Any], *, decomposition: dict[str, Any] | None = None) -> dict[str, Any]:
    """Adapt an explicit live issue; path/task authority comes only from explicit decomposition."""
    repository = str(issue["repository"])
    number = int(issue["number"])
    state = str(issue.get("state", "UNKNOWN")).upper()
    updated = str(issue.get("updated_at") or issue.get("revision") or "UNKNOWN")
    decomposition = decomposition or {}
    return {
        "project": _project(repository),
        "repository": repository,
        "source_type": "issue",
        "source_ref": f"{repository}#issue-{number}",
        "source_revision": updated,
        "source_state": state,
        "priority": _priority(decomposition.get("priority") or issue.get("priority"), "P5"),
        "paths": list(decomposition.get("paths", [])),
        "dependencies": list(decomposition.get("dependencies", [])),
        "task": decomposition.get("task"),
        "conflict_free": decomposition.get("conflict_free") is True,
        "actively_owned": issue.get("actively_owned") is True,
        "product_reality_required": decomposition.get("product_reality_required") is True,
        "base_sha": decomposition.get("base_sha"),
        "branch": decomposition.get("branch"),
        "verification": list(decomposition.get("verification", [])),
        "dependency_level": decomposition.get("dependency_level", 999),
        "downstream_unlock_count": decomposition.get("downstream_unlock_count", 0),
    }


def pull_request_candidate(pr: dict[str, Any]) -> dict[str, Any]:
    """Open PRs are existing-work candidates; exact head is their source revision."""
    repository = str(pr["repository"])
    number = int(pr["number"])
    head_sha = str(pr.get("head_sha") or "UNKNOWN")
    state = "MERGED" if pr.get("merged") is True else str(pr.get("state", "UNKNOWN")).upper()
    promotion = str(pr.get("promotion_state") or "P2").upper()
    return {
        "project": _project(repository),
        "repository": repository,
        "source_type": "pull_request",
        "source_ref": f"{repository}#pr-{number}",
        "source_revision": head_sha,
        "source_state": state,
        "priority": _priority(pr.get("priority"), promotion if promotion in {f"P{i}" for i in range(7)} else "P2"),
        "paths": list(pr.get("paths", [])),
        "dependencies": list(pr.get("dependencies", [])),
        "task": pr.get("task") or f"continue existing PR #{number}",
        "conflict_free": pr.get("conflict_free") is True,
        "actively_owned": pr.get("actively_owned") is True,
        "product_reality_required": pr.get("product_reality_required") is True,
        "base_sha": pr.get("base_sha"),
        "branch": pr.get("head_branch"),
        "verification": list(pr.get("verification", [])),
        "dependency_level": pr.get("dependency_level", 0),
        "downstream_unlock_count": pr.get("downstream_unlock_count", 0),
    }


def product_reality_candidate(debt: dict[str, Any]) -> dict[str, Any]:
    repository = str(debt["repository"])
    debt_id = str(debt["id"])
    revision = str(debt.get("revision") or debt.get("updated_at") or "UNKNOWN")
    return {
        "project": _project(repository),
        "repository": repository,
        "source_type": "product_reality_debt",
        "source_ref": f"{repository}#product-reality-{debt_id}",
        "source_revision": revision,
        "source_state": str(debt.get("state", "OPEN")).upper(),
        "priority": _priority(debt.get("priority"), "P4"),
        "paths": list(debt.get("paths", [])),
        "dependencies": list(debt.get("dependencies", [])),
        "task": debt.get("task"),
        "conflict_free": debt.get("conflict_free") is True,
        "actively_owned": debt.get("actively_owned") is True,
        "product_reality_required": True,
        "verification": list(debt.get("verification", [])),
    }


def adapt_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministically adapt explicit live source records; never parse prose into implementation scope."""
    decompositions = {str(item["source_ref"]): item for item in snapshot.get("decompositions", [])}
    result: list[dict[str, Any]] = []
    for issue in snapshot.get("issues", []):
        key = f"{issue['repository']}#issue-{int(issue['number'])}"
        result.append(issue_candidate(issue, decomposition=decompositions.get(key)))
    result.extend(pull_request_candidate(item) for item in snapshot.get("pull_requests", []))
    result.extend(product_reality_candidate(item) for item in snapshot.get("product_reality_debt", []))
    result.sort(key=lambda item: (item["project"], item["repository"], item["source_ref"]))
    return result
