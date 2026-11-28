"""N03: no-network tests of authorized Documents bridge and safe MCP projection."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from project_orchestrator.notion_n02_transport import NotionConnectionError, REPOSITORY
from project_orchestrator.notion_team_context import (
    DOCUMENTS_DATABASE_ID, _discover_documents, read_team_context, safe_team_context,
)
from project_orchestrator.notion_source_adapter import NotionSourceError

TOKEN = "ntn_" + "A" * 40
PAGE = "4269eaf5-47fa-81e6-8bf4-cc62c10d1ca1"
OTHER_DB = "4269eaf5-47fa-814f-9316-dca3f02e5d5d"


def document(parent: str = DOCUMENTS_DATABASE_ID) -> dict[str, Any]:
    return {
        "id": PAGE, "object": "page",
        "parent": {"type": "database_id", "database_id": parent},
        "archived": False,
    }


def response(*, documents: list[dict] | None = None, cursor: str | None = None) -> dict:
    return {
        "object": "list",
        "results": [document()] if documents is None else documents,
        "has_more": cursor is not None,
        "next_cursor": cursor,
    }


def record() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "surface": "KB", "kind": "notion_page",
        "source_id": PAGE,
        "source_uri": "https://www.notion.so/" + PAGE.replace("-", ""),
        "source_revision": "2026-11-28T12:00:00+00:00",
        "retrieved_at": "2026-11-28T17:00:00+00:00",
        "scope_type": "repository", "scope_id": REPOSITORY,
        "title": "Team architecture",
        "content": "Iron God Notion knowledge reference architecture",
        "block_ids": [PAGE],
        "evidence_state": "OBSERVED",
        "authority_class": "ADVISORY", "trust": "UNREVIEWED",
        "auto_promotion_allowed": False,
        "product_reality_may_be_inferred": False,
    }


class Client:
    def __init__(self, _: str):
        self.calls: list[str] = []

    def check_authentication(self) -> None:
        self.calls.append("auth")

    def _get(self, path: str) -> dict:
        self.calls.append(path)
        return {"object": "database", "id": DOCUMENTS_DATABASE_ID}

    def read_approved_page(self, page_id: str, *, repository: str,
                           allowed_page_ids: list[str],
                           required_parent_database_id: str) -> dict:
        assert page_id == PAGE
        assert repository == REPOSITORY
        assert allowed_page_ids == [PAGE]
        assert required_parent_database_id == DOCUMENTS_DATABASE_ID
        self.calls.append("read_page")
        from project_orchestrator.notion_source_adapter import _digest
        value = record()
        value["content_digest"] = _digest(value)
        return value


def test_live_read_projects_only_authorized_documents_with_provenance() -> None:
    instance = Client(TOKEN)
    result = read_team_context(
        credential_loader=lambda: TOKEN,
        client_factory=lambda _: instance,
        query=lambda token, payload: response(),
        terms=["architecture"], limit=3,
    )
    assert result["status"] == "OK"
    assert result["documents_discovered"] == 1
    assert result["documents_readable"] == 1
    assert result["documents_returned"] == 1
    assert result["items"][0]["authority_class"] == "ADVISORY"
    assert result["items"][0]["trust"] == "UNREVIEWED"
    assert result["items"][0]["content_digest"]
    assert result["freshness"] == "LIVE_API_READ_ON_EACH_CALL"
    assert result["can_execute"] is False
    assert result["can_write_notion"] is False
    assert instance.calls == [
        "auth", "/v1/databases/" + DOCUMENTS_DATABASE_ID, "read_page",
    ]


def test_private_memories_parent_is_rejected_before_reading_page() -> None:
    c = Client(TOKEN)
    with pytest.raises(NotionConnectionError, match="DOCUMENT_PARENT_MISMATCH"):
        read_team_context(
            credential_loader=lambda: TOKEN,
            client_factory=lambda _: c,
            query=lambda *_: response(documents=[document(OTHER_DB)]),
        )
    assert "read_page" not in c.calls


def test_empty_database_reports_no_fabricated_documents() -> None:
    result = read_team_context(
        credential_loader=lambda: TOKEN,
        client_factory=Client,
        query=lambda *_: response(documents=[]),
    )
    assert result["status"] == "OK"
    assert result["documents_discovered"] == 0
    assert result["items"] == []


@pytest.mark.parametrize("bad", [
    {"object": "list", "results": [], "has_more": True, "next_cursor": None},
    {"object": "list", "results": [document(), document()], "has_more": False, "next_cursor": None},
    {"object": "list", "results": ["not_a_page"], "has_more": False, "next_cursor": None},
])
def test_invalid_or_incomplete_listing_fails_closed(bad: dict) -> None:
    with pytest.raises(NotionConnectionError):
        _discover_documents(TOKEN, query=lambda *_: bad)


def test_cursor_loop_is_rejected() -> None:
    with pytest.raises(NotionConnectionError, match="INVALID_DOCUMENT_PAGINATION"):
        _discover_documents(TOKEN, query=lambda *_: response(documents=[], cursor="same"))


@pytest.mark.parametrize("terms,limit", [
    ("architecture", 5), (["a"] * 9, 5), (["ok"], 7), ([], 0),
])
def test_invalid_input_rejected_without_credential_access(terms: Any, limit: int) -> None:
    def forbidden() -> str:
        raise AssertionError("No credential should be read")
    with pytest.raises(NotionConnectionError):
        read_team_context(terms=terms, limit=limit, credential_loader=forbidden)


def test_not_projectable_document_is_reported_as_partial() -> None:
    class BadPage(Client):
        def read_approved_page(self, *args: Any, **kwargs: Any) -> dict:
            raise NotionSourceError("NESTED_OR_UNVERIFIED_BLOCKS")

    result = read_team_context(
        credential_loader=lambda: TOKEN,
        client_factory=BadPage,
        query=lambda *_: response(),
    )
    assert result["status"] == "PARTIAL"
    assert result["documents_skipped"]["DOCUMENT_NOT_PROJECTABLE"] == 1
    assert result["documents_returned"] == 0


def test_missing_credential_is_safe_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import project_orchestrator.notion_team_context as mod
    from project_orchestrator.notion_n02_secret_store import CredentialStoreError

    def no_secret() -> str:
        raise CredentialStoreError("no file")

    monkeypatch.setattr(mod, "load_token", no_secret)
    data = safe_team_context()
    assert data["status"] == "UNAVAILABLE"
    assert data["reason"] == "CREDENTIAL_UNAVAILABLE"
    assert data["can_execute"] is False


def test_document_query_uses_fixed_read_only_notion_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import urllib.request
    from unittest.mock import Mock
    import project_orchestrator.notion_team_context as mod

    mock_response = Mock()
    mock_response.read.return_value = b'{"object":"list","results":[],"has_more":false,"next_cursor":null}'
    mock_context = Mock()
    mock_context.__enter__ = Mock(return_value=mock_response)
    mock_context.__exit__ = Mock(return_value=False)
    mock_opener = Mock()
    mock_opener.open.return_value = mock_context
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: mock_opener)
    result = mod._post_documents_query(TOKEN, {"page_size": 30})
    assert result["object"] == "list"
    request = mock_opener.open.call_args.args[0]
    assert request.get_method() == "POST"
    assert request.full_url == (
        "https://api.notion.com/v1/databases/" + DOCUMENTS_DATABASE_ID + "/query"
    )
    assert json.loads(request.data) == {"page_size": 30}
    with pytest.raises(NotionConnectionError, match="INVALID_QUERY"):
        mod._post_documents_query(TOKEN, {"page_size": 30, "page_id": OTHER_DB})


def test_mismatched_database_is_rejected() -> None:
    class WrongDb(Client):
        def _get(self, path: str) -> dict:
            return {"object": "database", "id": OTHER_DB}
    with pytest.raises(NotionConnectionError, match="INVALID_DOCUMENTS_DATABASE"):
        read_team_context(
            credential_loader=lambda: TOKEN,
            client_factory=WrongDb,
            query=lambda *_: response(),
        )
