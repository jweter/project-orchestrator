"""Stable source-content fingerprints for failed development attempts.

This module never grants execution authority or resets an attempt budget.
"""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath


def source_fingerprint(files: dict[str, bytes]) -> str:
    """Hash exact source bytes and relative paths in a deterministic order."""
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        path = PurePosixPath(name.replace('\\', '/'))
        if path.is_absolute() or '..' in path.parts or not path.parts:
            raise ValueError('source paths must be repository-relative')
        if not isinstance(content, bytes):
            raise TypeError('source content must be bytes')
        for payload in (str(path).encode('utf-8'), content):
            digest.update(len(payload).to_bytes(8, 'big'))
            digest.update(payload)
    return digest.hexdigest()


def repeated_failed_source(files: dict[str, bytes], failed_fingerprints: set[str]) -> bool:
    """Identify exact repeats; distinct implementations remain eligible."""
    return source_fingerprint(files) in failed_fingerprints
