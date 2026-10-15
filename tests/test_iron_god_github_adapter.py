from __future__ import annotations

import inspect

import pytest

from project_orchestrator.iron_god_github_adapter import (
    GitHubPublicationAdapter,
    NotImplementedAdapter,
)
from project_orchestrator.iron_god_publication_job import validate_publication_job


def _job():
    return validate_publication_job(
        dict(
            publication_id="PUB-001",
            repository="demo-repo",
            milestone_id="M-001",
            accepted_sha="a" * 40,
            parent_accepted_sha=None,
            github_base_sha=None,
            verification_receipt_ref="receipt-001",
            product_reality_receipt_ref=None,
            required_ci=("build",),
            required_review_policy="one-approval",
            publication_branch="iron-god/publication/PUB-001",
            current_state="READY_TO_PUBLISH",
        )
    )


def test_not_implemented_adapter_satisfies_protocol():
    assert isinstance(NotImplementedAdapter(), GitHubPublicationAdapter)


@pytest.mark.parametrize(
    "method_name",
    ["push_branch", "open_pull_request", "get_ci_status", "get_review_status"],
)
def test_every_method_refuses_with_authorization_message(method_name):
    adapter = NotImplementedAdapter()
    method = getattr(adapter, method_name)
    with pytest.raises(NotImplementedError, match="not authorized"):
        method(_job())


def test_module_contains_no_network_capable_import():
    import project_orchestrator.iron_god_github_adapter as module

    source = inspect.getsource(module)
    forbidden = (
        "requests",
        "httpx",
        "urllib",
        "http.client",
        "socket",
        "import subprocess",
    )
    for token in forbidden:
        assert token not in source, f"module must not import/use {token!r}"
