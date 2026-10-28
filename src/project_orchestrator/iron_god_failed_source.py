"""Stable source-content fingerprints for failed development attempts.

Passive evidence only: this module never grants authority or changes retry budgets.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath


def validated_source_path(name: str) -> str:
    """Validate a repository-relative path before opening candidate source files."""
    if not isinstance(name, str) or not name or "\x00" in name or ":" in name:
        raise ValueError("invalid repository-relative source path")
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or PureWindowsPath(name).drive
        or PureWindowsPath(name).root
        or ".." in path.parts
        or not path.parts
    ):
        raise ValueError("source paths must be repository-relative")
    return str(path)


def read_candidate_sources(
    worktree: Path, changed_paths: list[str]
) -> dict[str, bytes]:
    """Read only files whose resolved paths remain inside the candidate worktree."""
    root = Path(worktree).resolve(strict=True)
    sources = {}
    for raw in changed_paths:
        relative = validated_source_path(raw)
        candidate = (root / relative).resolve(strict=True)
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise ValueError("candidate source is outside the worktree or not a file")
        sources[relative] = candidate.read_bytes()
    return sources


def source_fingerprint(files: dict[str, bytes]) -> str:
    """Hash exact source bytes and relative paths in a deterministic order."""
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        normalized = validated_source_path(name)
        if not isinstance(content, bytes):
            raise TypeError("source content must be bytes")
        for payload in (normalized.encode("utf-8"), content):
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return digest.hexdigest()


def repeated_failed_source(
    files: dict[str, bytes], failed_fingerprints: set[str]
) -> bool:
    """Identify exact repeats; distinct implementations remain eligible."""
    return source_fingerprint(files) in failed_fingerprints
