"""Opt-in read-only Notion HTTP transport for the N01 advisory source adapter.

N02 is a manually invoked connection test, not a production context injector.
No token is accepted via command-line arguments, environment variables, or logs.
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Collection, Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from .notion_source_adapter import MAX_BLOCKS, NotionSourceError, project_notion_page

API_ORIGIN = "https://api.notion.com"
API_VERSION = "2022-06-28"
TEST_PAGE_ID = "4269eaf5-47fa-8159-8302-fb53eb164372"
REPOSITORY = "jweter/project-orchestrator"
MAX_RESPONSE_BYTES = 1_048_576
MAX_PAGE_REQUESTS = 3


class NotionConnectionError(ValueError):
    """Safe diagnostic; messages never include credentials or response bodies."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_token(token: str) -> str:
    """Reject whitespace, control characters and oversized clipboard content."""
    if (
        not isinstance(token, str)
        or not 20 <= len(token) <= 2048
        or not token.isascii()
        or not token.isprintable()
        or any(c.isspace() for c in token)
    ):
        raise NotionConnectionError("INVALID_TOKEN_INPUT")
    return token


def normalize_page_id(page_id: str) -> str:
    try:
        if not isinstance(page_id, str):
            raise ValueError
        return str(UUID(page_id))
    except (ValueError, AttributeError) as exc:
        raise NotionConnectionError("INVALID_PAGE_ID") from exc


class _DenyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        # Never forward Authorization to a redirect destination.
        return None


def _https_get(path: str, token: str) -> Mapping[str, Any]:
    """Only GET api.notion.com paths, with system CA checks and no redirects."""
    if not path.startswith("/v1/") or "://" in path or "\\" in path or "#" in path:
        raise NotionConnectionError("INVALID_API_PATH")
    request = urllib.request.Request(
        API_ORIGIN + path,
        headers={
            "Authorization": "Bearer " + validate_token(token),
            "Notion-Version": API_VERSION,
            "Accept": "application/json",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _DenyRedirects(),
    )
    try:
        with opener.open(request, timeout=12) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        codes = {
            400: "HTTP_400",
            401: "HTTP_401_AUTHENTICATION",
            403: "HTTP_403_PERMISSION",
            404: "HTTP_404_PAGE_ACCESS",
            429: "HTTP_429_RATE_LIMIT",
        }
        raise NotionConnectionError(codes.get(exc.code, "HTTP_SERVER_ERROR")) from None
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError):
        raise NotionConnectionError("NETWORK_OR_TLS_ERROR") from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise NotionConnectionError("RESPONSE_TOO_LARGE")
    try:
        result = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise NotionConnectionError("INVALID_JSON_RESPONSE") from exc
    if not isinstance(result, dict):
        raise NotionConnectionError("INVALID_JSON_RESPONSE")
    return result


class NotionReadOnlyClient:
    """No write method; every request is read-only and explicitly scoped."""

    def __init__(
        self,
        token: str,
        *,
        request_json: Callable[[str, str], Mapping[str, Any]] | None = None,
    ):
        self._token = validate_token(token)
        self._request_json = request_json or _https_get

    def _get(self, path: str) -> Mapping[str, Any]:
        return self._request_json(path, self._token)

    def check_authentication(self) -> None:
        identity = self._get("/v1/users/me")
        if identity.get("object") != "user" or not identity.get("id"):
            raise NotionConnectionError("INVALID_IDENTITY_RESPONSE")

    def read_approved_page(
        self, page_id: str, *, repository: str,
        allowed_page_ids: Collection[str],
        required_parent_database_id: str | None = None,
    ) -> dict[str, Any]:
        target = normalize_page_id(page_id)
        allowed = {normalize_page_id(item) for item in allowed_page_ids}
        if not allowed or target not in allowed:
            raise NotionConnectionError("PAGE_NOT_ALLOWLISTED")
        # The caller cannot use this client as a general-purpose Notion scraper.
        if repository != REPOSITORY:
            raise NotionConnectionError("REPOSITORY_NOT_APPROVED")
        page = self._get("/v1/pages/" + target)
        if required_parent_database_id is not None:
            expected_parent = normalize_page_id(required_parent_database_id)
            parent = page.get("parent")
            if (
                not isinstance(parent, dict)
                or parent.get("type") != "database_id"
                or normalize_page_id(parent.get("database_id")) != expected_parent
            ):
                raise NotionConnectionError("DOCUMENT_PARENT_MISMATCH")
        collected: list[Any] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(MAX_PAGE_REQUESTS):
            path = "/v1/blocks/" + target + "/children?page_size=50"
            if cursor is not None:
                path += "&start_cursor=" + urllib.parse.quote(cursor, safe="")
            listing = self._get(path)
            if listing.get("object") != "list" or not isinstance(listing.get("results"), list):
                raise NotionConnectionError("INVALID_BLOCK_RESPONSE")
            collected.extend(listing["results"])
            if len(collected) > MAX_BLOCKS:
                raise NotionConnectionError("TOO_MANY_BLOCKS")
            if listing.get("has_more") is False and listing.get("next_cursor") is None:
                complete = {
                    "object": "list", "results": collected,
                    "has_more": False, "next_cursor": None,
                }
                return project_notion_page(
                    page, complete, allowed_page_ids=allowed, repository=repository,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                )
            next_cursor = listing.get("next_cursor")
            if (
                listing.get("has_more") is not True
                or not isinstance(next_cursor, str)
                or not next_cursor
                or len(next_cursor) > 2048
                or next_cursor in seen_cursors
            ):
                raise NotionConnectionError("INVALID_PAGINATION")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise NotionConnectionError("PAGINATION_LIMIT_REACHED")


SAFE_DIAGNOSTICS = {
    "INVALID_TOKEN_INPUT": "Token format is invalid. Copy the complete Notion integration token again.",
    "HTTP_401_AUTHENTICATION": "Notion rejected this token (401). Check the connection's API token.",
    "HTTP_403_PERMISSION": "Notion forbade this request (403). Check read capabilities.",
    "HTTP_404_PAGE_ACCESS": "Notion cannot see the test page (404). Check page access for this connection.",
    "HTTP_429_RATE_LIMIT": "Notion rate-limited this request (429). Retry manually later.",
    "NETWORK_OR_TLS_ERROR": "Network or TLS error. Check internet access and HTTPS inspection.",
    "PAGE_NOT_ALLOWLISTED": "The selected page is outside the approved test allowlist.",
    "REPOSITORY_NOT_APPROVED": "The repository is outside the approved N02 scope.",
    "PAGINATION_LIMIT_REACHED": "Page contents exceed the safe pagination limit.",
    "TOO_MANY_BLOCKS": "Page exceeds the N01 100-block limit.",
}


def diagnostic(exc: Exception) -> str:
    """Display only enumerated safe strings; never reveal token or HTTP body."""
    code = exc.code if isinstance(exc, NotionConnectionError) else (
        str(exc) if isinstance(exc, NotionSourceError) else "UNEXPECTED_ERROR"
    )
    return SAFE_DIAGNOSTICS.get(code, "Notion verification failed: " + (
        code if re.fullmatch(r"[A-Z][A-Z0-9_]{1,60}", code) else "UNEXPECTED_ERROR"
    ))
