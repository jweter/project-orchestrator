"""Bounded local Ollama outage recovery without granting work or changing worker authority."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_local_model
from scripts import iron_god_product_worker as worker


BASE = "b" * 40
REPO = "jweter/knowledge-engine-ai"


def approved_directed_item():
    return {
        "id": "AI-LOCAL-1",
        "repository": REPO,
        "status": "approved",
        "assignee": "iron-god",
        "level": 2,
        "depends_on": [],
        "direct": {"module_path": "src/pkg/feature.py", "test_path": "tests/test_feature.py"},
    }


def rejection(item, state):
    return backlog.exclusion(item, state, items=[item], done=set(), max_level=2)


def test_only_the_guarded_local_ollama_call_tags_a_refused_connection(monkeypatch):
    def refused(*_args, **_kwargs):
        raise ConnectionRefusedError(10061, "connection refused")

    monkeypatch.setattr(iron_god_local_model, "guarded_generate", refused)
    with pytest.raises(worker.LocalOllamaConnectionRefused, match="LOCAL_OLLAMA_CONNECTION_REFUSED"):
        worker.production_generate("bounded prompt", 128, lambda: None)

    def other_failure(*_args, **_kwargs):
        raise RuntimeError("repository test failure")

    monkeypatch.setattr(iron_god_local_model, "guarded_generate", other_failure)
    with pytest.raises(RuntimeError, match="repository test failure"):
        worker.production_generate("bounded prompt", 128, lambda: None)


class _QuietTrace:
    def __init__(self, *_args, **_kwargs):
        pass

    def phase(self, *_args):
        pass

    def wrap(self, _phase, fn):
        return fn


def _directed_handback(tmp_path, monkeypatch, failure, *, after_reply=False):
    monkeypatch.setattr(worker.execution_status, "Trace", _QuietTrace)
    monkeypatch.setattr(worker.execution_status, "dir_from_env", lambda: tmp_path)
    monkeypatch.setattr(worker, "_git", lambda _root, *args: SimpleNamespace(
        returncode=0, stdout="repair-branch" if "--abbrev-ref" in args else BASE
    ))
    monkeypatch.setattr(worker, "changed_paths", lambda _worktree: [])
    monkeypatch.setattr(worker, "validate_direct", lambda *_args: {"note": "approved directed edit"})

    def fail(_packet, _plan, _worktree, _base, handback, _finish, **_kwargs):
        if after_reply:
            handback["directed"] = {"prompt_tokens": 30, "output_tokens": 20}
        raise failure

    monkeypatch.setattr(worker, "execute_directed_slice", fail)
    packet = {
        "packet_id": "PRODUCT-TEST", "project": "Knowledge Engine AI", "repository": REPO,
        "branch": "repair-branch", "base_sha": BASE, "worktree": str(tmp_path),
        "slice": {"direct": {"approved": True}},
    }
    return worker.execute_product_packet(packet, authority=lambda: True)


def test_pre_reply_refusal_has_an_explicit_non_attempting_worker_outcome(tmp_path, monkeypatch):
    result = _directed_handback(
        tmp_path, monkeypatch, worker.LocalOllamaConnectionRefused("local endpoint refused"),
    )
    assert result["status"] == "ENVIRONMENT_FAILURE"
    assert result["summary"] == "LOCAL_OLLAMA_REFUSED_BEFORE_FIRST_MODEL_REPLY"


def test_refusal_after_a_reply_or_from_repository_tests_is_not_refunded(tmp_path, monkeypatch):
    after = _directed_handback(
        tmp_path, monkeypatch, worker.LocalOllamaConnectionRefused("local endpoint refused"),
        after_reply=True,
    )
    assert after["summary"].startswith("DIRECTED_SLICE_FAILED_TO_RUN")
    assert "BEFORE_FIRST_MODEL_REPLY" not in after["summary"]

    generic = _directed_handback(tmp_path, monkeypatch, ConnectionRefusedError("test server refused"))
    assert generic["summary"].startswith("DIRECTED_SLICE_FAILED_TO_RUN")
    assert "BEFORE_FIRST_MODEL_REPLY" not in generic["summary"]


def test_pre_reply_endpoint_outage_preserves_ledger_and_cools_down(tmp_path):
    item = approved_directed_item()
    row = backlog.record_item(
        tmp_path, item["id"], packet_id="P1", outcome=backlog.LOCAL_OLLAMA_REFUSAL,
    )
    assert row["packets"] == ["P1"]
    assert row["environment_packets"] == ["P1"]
    assert row["attempts"] == 0

    # Duplicate handback receipt must not manufacture another free attempt.
    same = backlog.record_item(
        tmp_path, item["id"], packet_id="P1", outcome=backlog.LOCAL_OLLAMA_REFUSAL,
    )
    assert same["packets"] == ["P1"] and same["attempts"] == 0
    current = backlog.load_state(tmp_path)
    assert rejection(item, current)[0] == "WAITING_FOR_LOCAL_OLLAMA"

    # The existing owner/lease/model gates still apply to subsequent dispatch;
    # the backlog's narrow cooldown alone does not confer execution authority.
    old = (datetime.now(UTC) - timedelta(
        seconds=backlog.LOCAL_OLLAMA_RETRY_COOLDOWN_SECONDS + 2
    )).isoformat()
    current["items"][item["id"]]["updated_at_utc"] = old
    assert rejection(item, current) is None

    current["items"][item["id"]]["updated_at_utc"] = "not-a-timestamp"
    assert rejection(item, current)[0] == "LOCAL_OLLAMA_OUTAGE_TIME_UNVERIFIED"


def test_general_environment_and_after_reply_failures_still_spend_attempts(tmp_path):
    item = approved_directed_item()
    for packet_id, outcome in (
        ("P1", "ENVIRONMENT_FAILURE: DIRECTED_SLICE_FAILED_TO_RUN: ConnectionRefusedError"),
        ("P2", "ENVIRONMENT_FAILURE: DIRECTED_SLICE_FAILED_TO_RUN: LOCAL_OLLAMA_REFUSED_AFTER_MODEL_REPLY"),
    ):
        row = backlog.record_item(tmp_path, item["id"], packet_id=packet_id, outcome=outcome)
    assert row["attempts"] == 2
    assert row["environment_packets"] == []


def test_endpoint_outage_refunds_remain_bounded_by_existing_attempt_budget(tmp_path):
    item = approved_directed_item()
    total = backlog.MAX_ENVIRONMENT_RETRIES + backlog.MAX_ITEM_ATTEMPTS
    for number in range(total):
        row = backlog.record_item(
            tmp_path, item["id"], packet_id=f"P{number}", outcome=backlog.LOCAL_OLLAMA_REFUSAL,
        )
    assert row["attempts"] == backlog.MAX_ITEM_ATTEMPTS
    assert len(row["packets"]) == total
    assert rejection(item, backlog.load_state(tmp_path))[0] == "ATTEMPTS_EXHAUSTED"
