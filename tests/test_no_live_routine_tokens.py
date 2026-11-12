"""No test may inherit the machine's real cloud-routine tokens (a test with them can fire a real, paid run)."""

from __future__ import annotations

import os

from project_orchestrator import iron_god_capable_builder as capable_builder
from project_orchestrator.claude_dispatch import ROUTINES


def test_no_real_routine_token_reaches_a_test():
    # tests/conftest.py removes them for every test; the preflight runs this with the developer's real environment
    assert [key for key in os.environ if key.startswith("CLAUDE_ROUTINE_")] == []
    assert all(env_name.startswith("CLAUDE_ROUTINE_") for _url, env_name in ROUTINES.values())  # the prefix covers every routine


def test_a_capable_request_is_not_dispatched_from_a_test_environment(tmp_path):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("a test reached the network")

    request = {"id": "RM-x", "repository": "jweter/example", "route": {"builder": "CAPABLE"}}
    result = capable_builder.dispatch_request(request, lane_root=tmp_path, enabled=True, opener=forbidden)
    assert result["dispatched"] is False and result["reason"] == "CAPABLE_BUILDER_UNCONFIGURED"
