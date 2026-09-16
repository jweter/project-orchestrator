from __future__ import annotations

import json

import pytest

from project_orchestrator.transient_memory import (
    MemoryReadIncomplete,
    TransientMemoryError,
    doctor,
    init_vault,
    read_memory,
    save_memory,
    search_memory,
)


def test_create_only_handoff_round_trip(tmp_path) -> None:
    root = tmp_path / "vault"
    init_vault(root)
    saved = save_memory(
        root,
        title="Rocksmith handoff",
        body="Continue deterministic launcher smoke coverage.",
        kind="handoff",
        source_harness="codex",
        target_harnesses=["claude"],
        tags=["rocksmith", "windows-e2e"],
        created_at="2026-09-16T14:00:00Z",
    )
    recalled = read_memory(root, saved["id"], target_harness="claude")
    result = search_memory(root, "launcher smoke", target_harness="claude")

    assert recalled["body"] == "Continue deterministic launcher smoke coverage."
    assert recalled["trust"] == "UNREVIEWED"
    assert recalled["authority"] == "CONTEXT_ONLY"
    assert recalled["auto_promotion_allowed"] is False
    assert result["complete_inventory"] is True
    assert result["recalled_context_is_authority"] is False
    assert result["results"][0]["id"] == saved["id"]
    assert doctor(root)["status"] == "GREEN"


def test_duplicate_create_is_rejected(tmp_path) -> None:
    kwargs = {
        "title": "handoff",
        "body": "Same bounded handoff.",
        "kind": "handoff",
        "source_harness": "codex",
        "target_harnesses": ["claude"],
        "created_at": "2026-09-16T14:00:00Z",
    }
    save_memory(tmp_path, **kwargs)
    with pytest.raises(TransientMemoryError, match="create-only"):
        save_memory(tmp_path, **kwargs)


def test_secret_like_content_is_rejected(tmp_path) -> None:
    with pytest.raises(TransientMemoryError, match="secret material"):
        save_memory(
            tmp_path,
            title="unsafe",
            body="api_key=literal-value",
            kind="handoff",
            source_harness="codex",
            target_harnesses=["claude"],
        )


def test_target_routing_is_a_filter_and_not_authority(tmp_path) -> None:
    saved = save_memory(
        tmp_path,
        title="handoff",
        body="For one harness only.",
        kind="handoff",
        source_harness="codex",
        target_harnesses=["claude"],
        created_at="2026-09-16T14:00:01Z",
    )
    with pytest.raises(TransientMemoryError, match="not targeted"):
        read_memory(tmp_path, saved["id"], target_harness="hermes")
    assert search_memory(tmp_path, "harness", target_harness="hermes")["results"] == []


def test_malformed_document_makes_direct_read_fail_closed_but_search_reports_partial_state(tmp_path) -> None:
    saved = save_memory(
        tmp_path,
        title="valid",
        body="Known context for retrieval.",
        kind="context",
        source_harness="codex",
        target_harnesses=["all"],
        created_at="2026-09-16T14:00:02Z",
    )
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(MemoryReadIncomplete, match="MEMORY_READ_INCOMPLETE"):
        read_memory(tmp_path, saved["id"])
    result = search_memory(tmp_path, "known context")
    assert result["complete_inventory"] is False
    assert result["results"][0]["id"] == saved["id"]
    assert any(item["code"] == "INVALID_DOCUMENT" for item in result["diagnostics"])


def test_document_cannot_be_rewritten_as_trusted_authority(tmp_path) -> None:
    saved = save_memory(
        tmp_path,
        title="handoff",
        body="Unreviewed context.",
        kind="handoff",
        source_harness="codex",
        target_harnesses=["all"],
        created_at="2026-09-16T14:00:03Z",
    )
    path = tmp_path / f"{saved['id']}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["trust"] = "VERIFIED"
    path.write_text(json.dumps(payload), encoding="utf-8")

    health = doctor(tmp_path)
    assert health["status"] == "RED"
    assert any(item["code"] == "INVALID_DOCUMENT" for item in health["problems"])
