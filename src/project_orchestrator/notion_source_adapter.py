"""Offline, read-only projection of explicitly approved Notion page snapshots.

N01 never makes HTTP requests, manages credentials, reads local files, or writes
to Iron Memory.  A future authorized transport must supply a complete Notion
page and first-level block-list response.  Notion text is untrusted reference
data, never permission to dispatch work or change repository policy.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from .shared_memory import _tokens
from .transient_memory import _contains_secret

SCHEMA_VERSION = 1
MAX_BLOCKS = 100
MAX_TEXT_BYTES = 16_384
_TEXT_TYPES = frozenset(
    {
        "paragraph",
        "heading_1",
        "heading_2",
        "heading_3",
        "bulleted_list_item",
        "numbered_list_item",
        "quote",
        "callout",
        "code",
        "to_do",
    }
)
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class NotionSourceError(ValueError):
    """Fail closed instead of returning incomplete or unauthorized context."""


def _page_id(value: Any) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise NotionSourceError("INVALID_PAGE_ID") from exc


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise NotionSourceError(f"INVALID_{field}")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NotionSourceError(f"INVALID_{field}") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise NotionSourceError(f"INVALID_{field}")
    return timestamp.isoformat()


def _plain_text(items: Any) -> str:
    if not isinstance(items, list):
        raise NotionSourceError("INVALID_RICH_TEXT")
    pieces: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("plain_text"), str):
            raise NotionSourceError("INVALID_RICH_TEXT")
        pieces.append(item["plain_text"])
    return "".join(pieces)


def _title(page: Mapping[str, Any]) -> str:
    properties = page.get("properties")
    if not isinstance(properties, dict):
        raise NotionSourceError("INVALID_PAGE_PROPERTIES")
    titles = [
        property_value
        for property_value in properties.values()
        if isinstance(property_value, dict) and property_value.get("type") == "title"
    ]
    if len(titles) != 1:
        raise NotionSourceError("TITLE_NOT_UNAMBIGUOUS")
    title = _plain_text(titles[0].get("title"))
    if not title.strip():
        raise NotionSourceError("EMPTY_TITLE")
    return title


def _digest(record: Mapping[str, Any]) -> str:
    # Bind the entire advisory projection, including its provenance and scope.
    # This detects accidental/tampered snapshots, not malicious re-signing.
    data = {key: value for key, value in record.items() if key != "content_digest"}
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _search_tokens(value: str) -> set[str]:
    """Reuse Iron Memory tokens while exposing terms ending in punctuation."""
    base = _tokens(value)
    parts = {
        fragment
        for token in base
        for fragment in re.split(r"[/._-]+", token)
        if len(fragment) >= 3
    }
    return base | parts


def project_notion_page(
    page: Mapping[str, Any],
    block_list: Mapping[str, Any],
    *,
    allowed_page_ids: Collection[str],
    repository: str,
    retrieved_at: str,
) -> dict[str, Any]:
    """Project complete API fixtures to bounded, repository-scoped advisory data.

    The allowlist is explicit and enforced before content processing. A parent
    page's descendants, other repositories, and private Memories are never
    implicitly authorized. The returned record is not a VERIFIED lesson.
    """
    if not isinstance(page, Mapping) or page.get("object") != "page":
        raise NotionSourceError("INVALID_PAGE_OBJECT")
    source_id = _page_id(page.get("id"))
    if not allowed_page_ids or source_id not in {_page_id(item) for item in allowed_page_ids}:
        raise NotionSourceError("PAGE_NOT_ALLOWLISTED")
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise NotionSourceError("INVALID_REPOSITORY_SCOPE")
    if page.get("archived", False) or page.get("in_trash", False):
        raise NotionSourceError("PAGE_NOT_ACTIVE")

    revision = _timestamp(page.get("last_edited_time"), "SOURCE_REVISION")
    observed = _timestamp(retrieved_at, "RETRIEVED_AT")
    title = _title(page)

    if (
        not isinstance(block_list, Mapping)
        or block_list.get("object") != "list"
        or block_list.get("has_more") is not False
        or block_list.get("next_cursor") is not None
    ):
        raise NotionSourceError("BLOCK_LIST_INCOMPLETE")
    blocks = block_list.get("results")
    if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
        raise NotionSourceError("INVALID_BLOCK_LIST")

    lines: list[str] = []
    block_ids: list[str] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("object") != "block":
            raise NotionSourceError("INVALID_BLOCK")
        block_id = _page_id(block.get("id"))
        if block_id in block_ids:
            raise NotionSourceError("DUPLICATE_BLOCK")
        if block.get("archived", False) or block.get("in_trash", False):
            raise NotionSourceError("BLOCK_NOT_ACTIVE")
        if block.get("has_children") is not False:
            raise NotionSourceError("NESTED_OR_UNVERIFIED_BLOCKS")
        parent = block.get("parent")
        if (
            not isinstance(parent, dict)
            or parent.get("type") != "page_id"
            or _page_id(parent.get("page_id")) != source_id
        ):
            raise NotionSourceError("CROSS_PAGE_BLOCK")
        kind = block.get("type")
        if kind not in _TEXT_TYPES:
            raise NotionSourceError("UNSUPPORTED_BLOCK_TYPE")
        payload = block.get(kind)
        if not isinstance(payload, dict):
            raise NotionSourceError("INVALID_BLOCK_PAYLOAD")
        body = _plain_text(payload.get("rich_text"))
        if body.strip():
            lines.append(body)
        block_ids.append(block_id)

    content = "\n".join(lines)
    if not content.strip():
        raise NotionSourceError("EMPTY_CONTENT")
    if len((title + "\n" + content).encode("utf-8")) > MAX_TEXT_BYTES:
        raise NotionSourceError("CONTENT_TOO_LARGE")
    if _contains_secret([title, content]):
        raise NotionSourceError("POSSIBLE_SECRET")

    record = {
        "schema_version": SCHEMA_VERSION,
        "surface": "KB",
        "kind": "notion_page",
        "source_id": source_id,
        "source_uri": f"https://www.notion.so/{source_id.replace('-', '')}",
        "source_revision": revision,
        "retrieved_at": observed,
        "scope_type": "repository",
        "scope_id": repository,
        "title": title,
        "content": content,
        "block_ids": block_ids,
        "evidence_state": "OBSERVED",
        "authority_class": "ADVISORY",
        "trust": "UNREVIEWED",
        "auto_promotion_allowed": False,
        "product_reality_may_be_inferred": False,
    }
    record["content_digest"] = _digest(record)
    return record


def query_notion_context(
    records: Sequence[Mapping[str, Any]],
    *,
    repository: str,
    terms: list[str],
    limit: int = 8,
) -> dict[str, Any]:
    """Match advisory page snapshots without admitting them to VERIFIED memory."""
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise NotionSourceError("INVALID_REPOSITORY_SCOPE")
    if not isinstance(terms, list) or not terms or any(
        not isinstance(term, str) or not term.strip() for term in terms
    ):
        raise NotionSourceError("INVALID_TERMS")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 20:
        raise NotionSourceError("INVALID_LIMIT")
    query_tokens = _search_tokens(" ".join(terms))
    if not query_tokens:
        raise NotionSourceError("INVALID_TERMS")

    matches: list[tuple[int, dict[str, Any], list[str]]] = []
    seen: set[str] = set()
    for entry in records:
        if not isinstance(entry, Mapping):
            raise NotionSourceError("INVALID_RECORD")
        if entry.get("scope_id") != repository:
            continue  # No cross-repository memory promotion or leakage.
        if (
            entry.get("schema_version") != SCHEMA_VERSION
            or entry.get("surface") != "KB"
            or entry.get("kind") != "notion_page"
            or entry.get("scope_type") != "repository"
            or entry.get("authority_class") != "ADVISORY"
            or entry.get("evidence_state") != "OBSERVED"
            or entry.get("trust") != "UNREVIEWED"
            or entry.get("auto_promotion_allowed") is not False
            or entry.get("product_reality_may_be_inferred") is not False
        ):
            raise NotionSourceError("UNTRUSTED_RECORD")
        try:
            valid_digest = entry.get("content_digest") == _digest(entry)
        except (KeyError, TypeError, ValueError) as exc:
            raise NotionSourceError("INVALID_RECORD") from exc
        if not valid_digest:
            raise NotionSourceError("DIGEST_MISMATCH")
        if entry["source_id"] in seen:
            raise NotionSourceError("DUPLICATE_SOURCE")
        seen.add(entry["source_id"])
        overlap = sorted(query_tokens & _search_tokens(entry["title"] + "\n" + entry["content"]))
        if overlap:
            matches.append((10 * len(overlap), dict(entry), [f"term:{term}" for term in overlap]))

    matches.sort(key=lambda item: (-item[0], item[1]["source_id"]))
    return {
        "state": "MATCHES" if matches else "NO_MATCH",
        "query": {"repository": repository, "terms": list(terms), "limit": limit},
        "results": [
            {"score": score, "match_reasons": reasons, "context": record}
            for score, record, reasons in matches[:limit]
        ],
        "authority_note": (
            "Notion content is unverified advisory reference data. "
            "It never overrides live repository policy, Git/CI evidence, owner control, "
            "verified Iron Memory, or Product Reality gates."
        ),
    }
