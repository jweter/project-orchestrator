"""Off-device checkpoint adapter -- CONTRACT ONLY (roadmap section 13).

Defines the interface any real off-device backup adapter (GitHub,
secondary Git remote, network storage, cloud object storage, encrypted
bundles) must satisfy. No real implementation is written in this
session -- every real adapter requires a network/remote operation,
out of scope here. `NotImplementedAdapter` structurally satisfies the
contract so calling code can be written and tested against it today;
every one of its methods refuses to run. A future, separately-
authorized slice supplies the real implementation against this exact
contract without changing any caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

_NOT_AUTHORIZED = (
    "Off-device backup operations are not authorized in this session; "
    "this adapter method must be implemented by a future, separately-"
    "authorized slice."
)


@dataclass(frozen=True)
class BackupResult:
    ok: bool
    backup_id: str | None
    error: str | None


@runtime_checkable
class OffDeviceCheckpointAdapter(Protocol):
    """Contract for a real off-device checkpoint backup adapter.

    A conforming implementation must: never overwrite a prior backup
    in place (each backup is immutable once written); report every
    failure as data (a BackupResult), never swallow it; never require
    disabling or weakening the owner-control gate to run.
    """

    def backup_commit(self, repository: str, commit_sha: str) -> BackupResult: ...

    def list_backups(self, repository: str) -> tuple[str, ...]: ...

    def verify_backup(self, repository: str, backup_id: str) -> bool: ...


class NotImplementedAdapter:
    """A conforming-shaped adapter whose every method refuses to run."""

    def backup_commit(self, repository: str, commit_sha: str) -> BackupResult:
        raise NotImplementedError(_NOT_AUTHORIZED)

    def list_backups(self, repository: str) -> tuple[str, ...]:
        raise NotImplementedError(_NOT_AUTHORIZED)

    def verify_backup(self, repository: str, backup_id: str) -> bool:
        raise NotImplementedError(_NOT_AUTHORIZED)
