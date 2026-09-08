from __future__ import annotations

import fnmatch
import re
from typing import Any

from .learning import validate_memory

_WORD = re.compile(r"[a-z0-9][a-z0-9_.-]*")


def _tokens(value: str) -> set[str]:
    return {token for token in _WORD.findall(value.lower()) if len(token) >= 3}


def _path_tokens(paths: list[str]) -> set[str]:
    tokens: set[str] = set()
    for path in paths:
        tokens |= _tokens(path)
        for part in path.replace("\\", "/").split("/"):
            tokens |= _tokens(part)
    return tokens


def retrieve_learning(
    memory: dict[str, Any],
    *,
    repository: str,
    changed_paths: list[str],
    change_summary: str = "",
    failure_class: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Return verified lessons relevant before a repository edit.

    Retrieval is deterministic and evidence-only. It never converts similarity
    into authority: callers still obey repository roadmap/architecture/policy.
    """
    normalized = validate_memory(memory)
    if not isinstance(repository, str) or not repository:
        raise ValueError("repository must be a non-empty string")
    if not isinstance(changed_paths, list) or not changed_paths or not all(
        isinstance(path, str) and path for path in changed_paths
    ):
        raise ValueError("changed_paths must contain at least one non-empty path")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")

    query_tokens = _path_tokens(changed_paths) | _tokens(change_summary)
    if failure_class:
        query_tokens |= _tokens(failure_class)

    matches: list[dict[str, Any]] = []
    for lesson in normalized["entries"]:
        if lesson["repository"] not in {repository, "jweter/project-orchestrator"}:
            continue

        searchable = " ".join(
            str(lesson[field])
            for field in (
                "symptom",
                "root_cause",
                "fix",
                "regression_protection",
                "residual_risk",
                "source_ref",
            )
        )
        lesson_tokens = _tokens(searchable)
        overlap = sorted(query_tokens & lesson_tokens)
        same_repo = lesson["repository"] == repository
        explicit_path_hits = sorted(
            path
            for path in changed_paths
            if path.lower() in searchable.lower()
            or any(fnmatch.fnmatch(path.lower(), pattern) for pattern in _extract_globs(searchable))
        )

        score = (4 if same_repo else 1) + len(overlap) + (3 * len(explicit_path_hits))
        if failure_class and failure_class.lower() in searchable.lower():
            score += 3

        # Portfolio lessons are surfaced only when they actually overlap the
        # planned change. Repository-local verified lessons remain relevant as
        # institutional context even when older records lack structured paths.
        if not same_repo and not overlap and not explicit_path_hits:
            continue
        if same_repo and score <= 4 and not overlap and not explicit_path_hits:
            continue

        matches.append(
            {
                "lesson_id": lesson["lesson_id"],
                "repository": lesson["repository"],
                "source_ref": lesson["source_ref"],
                "score": score,
                "matched_tokens": overlap,
                "matched_paths": explicit_path_hits,
                "root_cause": lesson["root_cause"],
                "prevention_rule": lesson["regression_protection"],
                "residual_risk": lesson["residual_risk"],
            }
        )

    matches.sort(key=lambda item: (-item["score"], item["lesson_id"]))
    selected = matches[:limit]
    return {
        "schema_version": 1,
        "repository": repository,
        "changed_paths": sorted(set(changed_paths)),
        "change_summary": change_summary,
        "failure_class": failure_class,
        "verified_lessons_considered": len(normalized["entries"]),
        "relevant_lessons": selected,
        "learning_gate": "APPLY_RELEVANT_PREVENTION" if selected else "NO_RELEVANT_VERIFIED_LESSON",
        "authority_note": "retrieved lessons inform prevention but never override repository roadmap, architecture, policy, CI, provenance/licensing, or Product Reality authority",
    }


def _extract_globs(text: str) -> list[str]:
    return [
        token.lower()
        for token in text.split()
        if "*" in token and "/" in token and len(token) < 200
    ]
