"""Proves the repository-scope boundary without any real dispatch/build."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from project_orchestrator.iron_god_scoped_dispatch_authorization import (
    ScopedAuthorizationError,
    scoped_worker_executor,
)


@dataclass(frozen=True)
class FakeClaim:
    slice_id: str


KE_CORE = "jweter/knowledge-engine-core"
OTHER_REPO = "jweter/some-other-repo"


def base_packet(**overrides):
    packet = {"slice_id": "KE-CORE-S1-HYBRID-SEARCH", "repository": KE_CORE}
    packet.update(overrides)
    return packet


def base_task(**overrides):
    task = {"id": "KE-CORE-S1-HYBRID-SEARCH", "base_sha": "a" * 40}
    task.update(overrides)
    return task


def test_authorized_repository_reaches_inner_executor():
    calls = []

    def inner(reserved, task):
        calls.append((reserved, task))
        return "EXECUTED"

    claim = FakeClaim(slice_id="KE-CORE-S1-HYBRID-SEARCH")
    result = scoped_worker_executor(
        claim,
        base_task(),
        packet=base_packet(),
        authorized_repositories=frozenset({KE_CORE}),
        inner_executor=inner,
    )
    assert result == "EXECUTED"
    assert calls == [(claim, base_task())]


def test_out_of_scope_repository_never_reaches_inner_executor():
    calls = []

    def inner(reserved, task):
        calls.append(1)
        return "SHOULD_NOT_HAPPEN"

    claim = FakeClaim(slice_id="SOME-OTHER-SLICE")
    with pytest.raises(
        ScopedAuthorizationError, match="REPOSITORY_NOT_IN_AUTHORIZED_SCOPE"
    ):
        scoped_worker_executor(
            claim,
            base_task(id="SOME-OTHER-SLICE"),
            packet=base_packet(slice_id="SOME-OTHER-SLICE", repository=OTHER_REPO),
            authorized_repositories=frozenset({KE_CORE}),
            inner_executor=inner,
        )
    assert calls == []


def test_empty_authorized_set_refuses_everything():
    calls = []
    claim = FakeClaim(slice_id="KE-CORE-S1-HYBRID-SEARCH")
    with pytest.raises(
        ScopedAuthorizationError, match="REPOSITORY_NOT_IN_AUTHORIZED_SCOPE"
    ):
        scoped_worker_executor(
            claim,
            base_task(),
            packet=base_packet(),
            authorized_repositories=frozenset(),
            inner_executor=lambda *_a: calls.append(1),
        )
    assert calls == []


def test_packet_missing_repository_is_refused():
    claim = FakeClaim(slice_id="KE-CORE-S1-HYBRID-SEARCH")
    with pytest.raises(
        ScopedAuthorizationError, match="DISPATCH_HANDOFF_REPOSITORY_MISSING"
    ):
        scoped_worker_executor(
            claim,
            base_task(),
            packet=base_packet(repository=None),
            authorized_repositories=frozenset({KE_CORE}),
            inner_executor=lambda *_a: pytest.fail("must never be called"),
        )


def test_packet_not_a_mapping_is_refused():
    claim = FakeClaim(slice_id="KE-CORE-S1-HYBRID-SEARCH")
    with pytest.raises(
        ScopedAuthorizationError, match="DISPATCH_HANDOFF_PACKET_MISSING"
    ):
        scoped_worker_executor(
            claim,
            base_task(),
            packet=None,
            authorized_repositories=frozenset({KE_CORE}),
            inner_executor=lambda *_a: pytest.fail("must never be called"),
        )


def test_malformed_authorized_set_is_refused():
    claim = FakeClaim(slice_id="KE-CORE-S1-HYBRID-SEARCH")
    with pytest.raises(
        ScopedAuthorizationError, match="AUTHORIZED_REPOSITORY_SET_MALFORMED"
    ):
        scoped_worker_executor(
            claim,
            base_task(),
            packet=base_packet(),
            authorized_repositories={KE_CORE},  # a plain set, not frozenset
            inner_executor=lambda *_a: pytest.fail("must never be called"),
        )


def test_packet_slice_identity_must_match_the_reserved_claim():
    """A packet for a DIFFERENT slice than the one actually reserved must
    never be used to decide this dispatch's repository scope -- the exact
    kind of two-independently-supplied-identity drift this session's
    earlier task-construction bug was."""
    claim = FakeClaim(slice_id="KE-CORE-S1-HYBRID-SEARCH")
    with pytest.raises(
        ScopedAuthorizationError, match="PACKET_SLICE_IDENTITY_MISMATCH"
    ):
        scoped_worker_executor(
            claim,
            base_task(),
            packet=base_packet(slice_id="A-DIFFERENT-SLICE"),
            authorized_repositories=frozenset({KE_CORE}),
            inner_executor=lambda *_a: pytest.fail("must never be called"),
        )


def test_task_identity_must_match_the_reserved_claim():
    claim = FakeClaim(slice_id="KE-CORE-S1-HYBRID-SEARCH")
    with pytest.raises(ScopedAuthorizationError, match="TASK_SLICE_IDENTITY_MISMATCH"):
        scoped_worker_executor(
            claim,
            base_task(id="A-DIFFERENT-SLICE"),
            packet=base_packet(),
            authorized_repositories=frozenset({KE_CORE}),
            inner_executor=lambda *_a: pytest.fail("must never be called"),
        )


def test_inner_executor_exception_propagates_unchanged():
    def inner(_reserved, _task):
        raise RuntimeError("real builder failed")

    claim = FakeClaim(slice_id="KE-CORE-S1-HYBRID-SEARCH")
    with pytest.raises(RuntimeError, match="real builder failed"):
        scoped_worker_executor(
            claim,
            base_task(),
            packet=base_packet(),
            authorized_repositories=frozenset({KE_CORE}),
            inner_executor=inner,
        )


def test_multiple_authorized_repositories_can_coexist():
    calls = []

    def inner(reserved, task):
        calls.append(reserved.slice_id)
        return "EXECUTED"

    claim_a = FakeClaim(slice_id="KE-CORE-S1-HYBRID-SEARCH")
    claim_b = FakeClaim(slice_id="OTHER-REPO-SLICE")
    authorized = frozenset({KE_CORE, OTHER_REPO})

    scoped_worker_executor(
        claim_a,
        base_task(),
        packet=base_packet(),
        authorized_repositories=authorized,
        inner_executor=inner,
    )
    scoped_worker_executor(
        claim_b,
        base_task(id="OTHER-REPO-SLICE"),
        packet=base_packet(slice_id="OTHER-REPO-SLICE", repository=OTHER_REPO),
        authorized_repositories=authorized,
        inner_executor=inner,
    )
    assert calls == ["KE-CORE-S1-HYBRID-SEARCH", "OTHER-REPO-SLICE"]
