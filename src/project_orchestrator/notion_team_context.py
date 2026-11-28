"""On-demand, read-only Notion Documents context for Iron God and MCP teammates.

The source is the explicitly shared Documents database, never private Memories.
Nothing is scheduled, cached as VERIFIED memory, or injected into execution.
Notion is untrusted ADVISORY text and cannot alter controller/merge policy.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .notion_n02_secret_store import CredentialStoreError, load_token
from .notion_n02_transport import (
    API_ORIGIN, API_VERSION, MAX_RESPONSE_BYTES, REPOSITORY,
    _DenyRedirects, NotionConnectionError, NotionReadOnlyClient,
    diagnostic, normalize_page_id, validate_token,
)
from .notion_source_adapter import NotionSourceError, query_notion_context

DOCUMENTS_DATABASE_ID = "4269eaf5-47fa-81d8-a7b3-eca9c5f2d2bd"
MAX_DOCUMENTS = 30
MAX_QUERY_PAGES = 3
MAX_RETURNED_DOCUMENTS = 6
MAX_EXCERPT_CHARS = 3000
MAX_TERMS = 8
MAX_TERM_CHARS = 80


def _post_documents_query(token: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """POST is used ONLY for Notion's read-only database query endpoint."""
    validate_token(token)
    if not isinstance(payload, Mapping) or not set(payload).issubset(
        {"page_size", "start_cursor"}
    ) or payload.get("page_size") != 30:
        raise NotionConnectionError("INVALID_QUERY")
    cursor = payload.get("start_cursor")
    if cursor is not None and (
        not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048
    ):
        raise NotionConnectionError("INVALID_QUERY")
    request = urllib.request.Request(
        f"{API_ORIGIN}/v1/databases/{DOCUMENTS_DATABASE_ID}/query",
        data=json.dumps(dict(payload), separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Notion-Version": API_VERSION,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _DenyRedirects(),
    )
    try:
        with opener.open(request, timeout=12) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        codes = {
            400: "HTTP_400",
            401: "HTTP_401_AUTHENTICATION",
            403: "HTTP_403_PERMISSION",
            404: "HTTP_404_DOCUMENTS_DATABASE",
            429: "HTTP_429_RATE_LIMIT",
        }
        raise NotionConnectionError(codes.get(exc.code, "HTTP_SERVER_ERROR")) from None
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError):
        raise NotionConnectionError("NETWORK_OR_TLS_ERROR") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise NotionConnectionError("RESPONSE_TOO_LARGE")
    try:
        result = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise NotionConnectionError("INVALID_JSON_RESPONSE") from exc
    if not isinstance(result, dict):
        raise NotionConnectionError("INVALID_JSON_RESPONSE")
    return result


def _discover_documents(
    token: str,
    *,
    query: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    pages: list[Mapping[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    seen_pages: set[str] = set()
    for _ in range(MAX_QUERY_PAGES):
        payload: dict[str, Any] = {"page_size": 30}
        if cursor is not None:
            payload["start_cursor"] = cursor
        batch = query(token, payload)
        if (
            not isinstance(batch, Mapping)
            or batch.get("object") != "list"
            or not isinstance(batch.get("results"), list)
        ):
            raise NotionConnectionError("INVALID_DOCUMENT_LIST")
        for page in batch["results"]:
            if not isinstance(page, Mapping) or page.get("object") != "page":
                raise NotionConnectionError("INVALID_DOCUMENT_LIST")
            page_id = normalize_page_id(page.get("id"))
            parent = page.get("parent")
            if (
                not isinstance(parent, dict)
                or parent.get("type") != "database_id"
                or normalize_page_id(parent.get("database_id")) != DOCUMENTS_DATABASE_ID
            ):
                raise NotionConnectionError("DOCUMENT_PARENT_MISMATCH")
            if page_id in seen_pages:
                raise NotionConnectionError("DUPLICATE_DOCUMENT")
            seen_pages.add(page_id)
            pages.append(page)
            if len(pages) > MAX_DOCUMENTS:
                raise NotionConnectionError("DOCUMENT_LIMIT_REACHED")
        if batch.get("has_more") is False and batch.get("next_cursor") is None:
            return pages
        next_cursor = batch.get("next_cursor")
        if (
            batch.get("has_more") is not True
            or not isinstance(next_cursor, str)
            or not 1 <= len(next_cursor) <= 2048
            or next_cursor in seen_cursors
        ):
            raise NotionConnectionError("INVALID_DOCUMENT_PAGINATION")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise NotionConnectionError("DOCUMENT_LIST_INCOMPLETE")


def read_team_context(
    *,
    terms: Sequence[str] = (),
    limit: int = 5,
    repository: str = REPOSITORY,
    credential_loader: Callable[[], str] = load_token,
    client_factory: Callable[[str], NotionReadOnlyClient] = NotionReadOnlyClient,
    query: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] = _post_documents_query,
) -> dict[str, Any]:
    """Fetch fresh pages under the *Documents* database. Never caches or writes.

    The MCP caller cannot select a different Notion workspace, database, page,
    repository, host or auth token. All page bodies are revalidated by N01.
    """
    if repository != REPOSITORY:
        raise NotionConnectionError("REPOSITORY_NOT_APPROVED")
    if (
        not isinstance(limit, int) or isinstance(limit, bool)
        or not 1 <= limit <= MAX_RETURNED_DOCUMENTS
    ):
        raise NotionConnectionError("INVALID_LIMIT")
    if (
        isinstance(terms, (str, bytes))
        or not isinstance(terms, Sequence)
        or len(terms) > MAX_TERMS
        or any(
            not isinstance(term, str)
            or not 1 <= len(term.strip()) <= MAX_TERM_CHARS
            for term in terms
        )
    ):
        raise NotionConnectionError("INVALID_TERMS")
    normalized_terms = [term.strip() for term in terms]

    token = credential_loader()
    client = client_factory(token)
    client.check_authentication()
    metadata = client._get("/v1/databases/" + DOCUMENTS_DATABASE_ID)
    if (
        metadata.get("object") != "database"
        or normalize_page_id(metadata.get("id")) != DOCUMENTS_DATABASE_ID
        or metadata.get("archived", False)
        or metadata.get("in_trash", False)
    ):
        raise NotionConnectionError("INVALID_DOCUMENTS_DATABASE")
    documents = _discover_documents(token, query=query)
    records: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for doc in documents:
        if doc.get("archived", False) or doc.get("in_trash", False):
            skipped["ARCHIVED"] = skipped.get("ARCHIVED", 0) + 1
            continue
        try:
            record = client.read_approved_page(
                doc["id"],
                repository=repository,
                allowed_page_ids=[doc["id"]],
                required_parent_database_id=DOCUMENTS_DATABASE_ID,
            )
            records.append(record)
        except (NotionConnectionError, NotionSourceError) as exc:
            # Unsupported block structure or redacted sensitive content is not
            # silently treated as fresh/complete team knowledge.
            code = str(exc)
            if code not in {
                "NETWORK_OR_TLS_ERROR", "HTTP_401_AUTHENTICATION", "HTTP_403_PERMISSION",
                "HTTP_404_PAGE_ACCESS", "HTTP_429_RATE_LIMIT",
            }:
                code = "DOCUMENT_NOT_PROJECTABLE"
            skipped[code] = skipped.get(code, 0) + 1

    if normalized_terms and records:
        ranked = query_notion_context(
            records, repository=repository, terms=normalized_terms, limit=limit,
        )
        results = [item["context"] for item in ranked["results"]]
    elif normalized_terms:
        results = []
    else:
        results = sorted(
            records, key=lambda item: (item["source_revision"], item["source_id"]),
            reverse=True,
        )[:limit]

    # A digest refers to the full validated N01 source. The excerpt is
    # deliberately truncated for a bounded MCP response; it is not the source.
    projected = [
        {
            "source_id": rec["source_id"],
            "source_uri": rec["source_uri"],
            "source_revision": rec["source_revision"],
            "retrieved_at": rec["retrieved_at"],
            "title": rec["title"],
            "excerpt": rec["content"][:MAX_EXCERPT_CHARS],
            "excerpt_truncated": len(rec["content"]) > MAX_EXCERPT_CHARS,
            "content_digest": rec["content_digest"],
            "trust": "UNREVIEWED",
            "authority_class": "ADVISORY",
        }
        for rec in results
    ]
    return {
        "status": "PARTIAL" if skipped else "OK",
        "source": "NOTION_DOCUMENTS_DATABASE_ONLY",
        "documents_discovered": len(documents),
        "documents_readable": len(records),
        "documents_skipped": skipped,
        "documents_returned": len(projected),
        "terms": normalized_terms,
        "items": projected,
        "authority": "ADVISORY_ONLY_NOT_INSTRUCTIONS_OR_EXECUTION_AUTHORITY",
        "can_execute": False,
        "can_write_notion": False,
        "scheduled": False,
        "freshness": "LIVE_API_READ_ON_EACH_CALL",
    }


def safe_team_context(*, terms: Sequence[str] = (), limit: int = 5) -> dict[str, Any]:
    """For CLI/MCP: return a safe unavailable result without leaking internals."""
    try:
        return read_team_context(terms=terms, limit=limit)
    except (NotionConnectionError, NotionSourceError, CredentialStoreError) as exc:
        return {
            "status": "UNAVAILABLE",
            "reason": (
                "CREDENTIAL_UNAVAILABLE" if isinstance(exc, CredentialStoreError)
                else diagnostic(exc)
            ),
            "can_execute": False,
            "can_write_notion": False,
            "source": "NOTION_DOCUMENTS_DATABASE_ONLY",
        }
    except Exception:
        return {
            "status": "UNAVAILABLE", "reason": "UNEXPECTED_ERROR",
            "can_execute": False, "can_write_notion": False,
            "source": "NOTION_DOCUMENTS_DATABASE_ONLY",
        }
