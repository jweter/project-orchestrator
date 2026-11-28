"""Networkless regression tests for the N02 read-only transport."""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pytest

from project_orchestrator.notion_n02_transport import (
    _DenyRedirects,
    _https_get,
    NotionConnectionError,
    NotionReadOnlyClient,
    REPOSITORY,
    TEST_PAGE_ID,
    diagnostic,
    validate_token,
)

TOKEN = "ntn_" + "A" * 40
BLOCK_ID = "00000000-0000-4000-8000-000000000002"


def page_fixture() -> dict:
    return {
        "object": "page", "id": TEST_PAGE_ID, "archived": False,
        "last_edited_time": "2026-11-28T12:30:00Z",
        "properties": {
            "Name": {"type": "title", "title": [
                {"type": "text", "plain_text": "Iron God knowledge"}
            ]}
        },
    }


def block_fixture(block_id: str = BLOCK_ID) -> dict:
    return {
        "object": "block", "id": block_id, "type": "paragraph",
        "archived": False, "has_children": False,
        "parent": {"type": "page_id", "page_id": TEST_PAGE_ID},
        "paragraph": {"rich_text": [
            {"type": "text", "plain_text": "Read-only roadmap guidance."}
        ]},
    }


def listing(blocks: list[dict], *, cursor: str | None = None) -> dict:
    return {
        "object": "list", "results": blocks,
        "has_more": cursor is not None, "next_cursor": cursor,
    }


def fake_transport(path: str, token: str) -> dict:
    assert token == TOKEN
    if path == "/v1/users/me":
        return {"object": "user", "type": "bot", "id": "bot-1"}
    if path == "/v1/pages/" + TEST_PAGE_ID:
        return page_fixture()
    if path == "/v1/blocks/" + TEST_PAGE_ID + "/children?page_size=50":
        return listing([block_fixture()])
    raise AssertionError("Unexpected URL: " + path)


def test_auth_and_page_projection_remain_advisory() -> None:
    client = NotionReadOnlyClient(TOKEN, request_json=fake_transport)
    client.check_authentication()
    item = client.read_approved_page(
        TEST_PAGE_ID, repository=REPOSITORY, allowed_page_ids=[TEST_PAGE_ID],
    )
    assert item["source_id"] == TEST_PAGE_ID
    assert item["title"] == "Iron God knowledge"
    assert item["content"] == "Read-only roadmap guidance."
    assert item["evidence_state"] == "OBSERVED"
    assert item["trust"] == "UNREVIEWED"
    assert item["authority_class"] == "ADVISORY"
    assert item["auto_promotion_allowed"] is False
    assert item["product_reality_may_be_inferred"] is False
    assert "content_digest" in item


def test_scope_and_allowlist_block_network_access() -> None:
    requests = []
    client = NotionReadOnlyClient(TOKEN, request_json=lambda path, _: requests.append(path))
    with pytest.raises(NotionConnectionError, match="PAGE_NOT_ALLOWLISTED"):
        client.read_approved_page(TEST_PAGE_ID, repository=REPOSITORY, allowed_page_ids=[])
    with pytest.raises(NotionConnectionError, match="REPOSITORY_NOT_APPROVED"):
        client.read_approved_page(
            TEST_PAGE_ID, repository="jweter/VoiceStudio", allowed_page_ids=[TEST_PAGE_ID],
        )
    with pytest.raises(NotionConnectionError, match="INVALID_PAGE_ID"):
        client.read_approved_page(
            "https://evil.example.com/", repository=REPOSITORY,
            allowed_page_ids=[TEST_PAGE_ID],
        )
    assert requests == []


def test_two_paginated_responses_are_combined() -> None:
    calls = []
    def transport(path: str, token: str) -> dict:
        calls.append(path)
        if path.endswith("page_size=50"):
            return listing([block_fixture()], cursor="next/one")
        if "start_cursor=next%2Fone" in path:
            return listing([block_fixture("00000000-0000-4000-8000-000000000003")])
        return fake_transport(path, token)
    item = NotionReadOnlyClient(TOKEN, request_json=transport).read_approved_page(
        TEST_PAGE_ID, repository=REPOSITORY, allowed_page_ids=[TEST_PAGE_ID],
    )
    assert len(item["block_ids"]) == 2
    assert any("start_cursor=next%2Fone" in path for path in calls)


@pytest.mark.parametrize("mode", ["missing_cursor", "repeated_cursor", "oversized"])
def test_incomplete_pagination_fails_closed(mode: str) -> None:
    def transport(path: str, token: str) -> dict:
        if "/children" not in path:
            return fake_transport(path, token)
        if mode == "missing_cursor":
            return {"object": "list", "results": [], "has_more": True, "next_cursor": None}
        if mode == "repeated_cursor":
            return listing([block_fixture()], cursor="same")
        return listing([block_fixture()] * 101)
    client = NotionReadOnlyClient(TOKEN, request_json=transport)
    with pytest.raises(NotionConnectionError):
        client.read_approved_page(
            TEST_PAGE_ID, repository=REPOSITORY, allowed_page_ids=[TEST_PAGE_ID],
        )


@pytest.mark.parametrize("token", [
    "short", "ntn_" + "x" * 20 + "\n", "ntn_" + "x" * 20 + " ", "n" * 2050,
    "ntn_" + "x" * 20 + "\x1b", "ntn_" + "é" * 25, "",
])
def test_invalid_token_is_rejected_before_transport(token: str) -> None:
    with pytest.raises(NotionConnectionError, match="INVALID_TOKEN_INPUT"):
        validate_token(token)


def test_network_client_allows_get_only_and_sanitizes_http_errors() -> None:
    with patch("urllib.request.build_opener") as opener:
        response = opener.return_value.open.return_value.__enter__.return_value
        response.read.return_value = json.dumps({"object": "user", "id": "test"}).encode()
        result = _https_get("/v1/users/me", TOKEN)
        assert result["object"] == "user"
        request = opener.return_value.open.call_args.args[0]
        assert request.get_method() == "GET"
        assert request.full_url == "https://api.notion.com/v1/users/me"
        assert request.get_header("Authorization") == "Bearer " + TOKEN
        opener.assert_called_once()
    with patch("urllib.request.build_opener") as opener:
        opener.return_value.open.side_effect = urllib.error.HTTPError(
            "https://api.notion.com/v1/users/me", 401, "invalid token", {}, None,
        )
        with pytest.raises(NotionConnectionError, match="HTTP_401_AUTHENTICATION") as error:
            _https_get("/v1/users/me", TOKEN)
        assert TOKEN not in str(error.value)
        assert "invalid token" not in diagnostic(error.value)


def test_redirects_are_blocked_and_invalid_paths_fail() -> None:
    handler = _DenyRedirects()
    assert handler.redirect_request(None, None, 302, "redirect", None, "https://evil.test") is None
    with pytest.raises(NotionConnectionError, match="INVALID_API_PATH"):
        _https_get("https://evil.test/v1/users/me", TOKEN)
    with pytest.raises(NotionConnectionError, match="INVALID_API_PATH"):
        _https_get("/v1/users/me#exfiltrate", TOKEN)


def test_bad_bot_identity_is_rejected() -> None:
    client = NotionReadOnlyClient(TOKEN, request_json=lambda *_: {"object": "error"})
    with pytest.raises(NotionConnectionError, match="INVALID_IDENTITY_RESPONSE"):
        client.check_authentication()
