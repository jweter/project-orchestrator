from __future__ import annotations

import inspect

import pytest

from project_orchestrator.iron_god_offdevice_checkpoint import (
    NotImplementedAdapter,
    OffDeviceCheckpointAdapter,
)


def test_not_implemented_adapter_satisfies_protocol():
    assert isinstance(NotImplementedAdapter(), OffDeviceCheckpointAdapter)


@pytest.mark.parametrize(
    "method_name", ["backup_commit", "list_backups", "verify_backup"]
)
def test_every_method_refuses_with_authorization_message(method_name):
    adapter = NotImplementedAdapter()
    method = getattr(adapter, method_name)
    with pytest.raises(NotImplementedError, match="not authorized"):
        if method_name == "backup_commit":
            method("demo-repo", "a" * 40)
        elif method_name == "list_backups":
            method("demo-repo")
        else:
            method("demo-repo", "backup-001")


def test_module_contains_no_network_capable_import():
    import project_orchestrator.iron_god_offdevice_checkpoint as module

    source = inspect.getsource(module)
    forbidden = (
        "requests",
        "httpx",
        "urllib",
        "http.client",
        "socket",
        "import subprocess",
        "boto3",
    )
    for token in forbidden:
        assert token not in source, f"module must not import/use {token!r}"
