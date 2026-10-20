"""Anchored path-component matching for explicitly granted worker slice paths.

Plain path scopes retain their historical subtree meaning. In glob scopes a
single '*' matches within one component; only '**' matches directories.
The caller applies forbidden scopes first and still enforces all other gates.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from functools import lru_cache


def matches_authorized_path(path: str, scope: str) -> bool:
    """Match a repository-relative file path against one declared scope."""
    normalized = path.replace("\\", "/").strip("/")
    components = normalized.split("/")
    patterns = scope.split("/")
    if not any(any(char in part for char in "*?[") for part in patterns):
        return normalized == scope or normalized.startswith(scope + "/")

    @lru_cache(maxsize=None)
    def visit(path_index: int, scope_index: int) -> bool:
        if scope_index == len(patterns):
            return path_index == len(components)
        if patterns[scope_index] == "**":
            return visit(path_index, scope_index + 1) or (
                path_index < len(components) and visit(path_index + 1, scope_index)
            )
        return (
            path_index < len(components)
            and fnmatchcase(components[path_index], patterns[scope_index])
            and visit(path_index + 1, scope_index + 1)
        )

    return visit(0, 0)
