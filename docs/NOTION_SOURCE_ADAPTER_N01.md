# N01: Offline Notion source adapter

**Status:** Offline contract only. No Notion HTTP client, OAuth handler,
credential storage, scheduler, SQLite migration, worker hook, write
capability, or production activation is introduced.

## Purpose and existing boundaries

The pure adapter in src/project_orchestrator/notion_source_adapter.py projects
a *complete*, caller-supplied Notion REST page and its first-level block list
into a bounded, repository-scoped KB reference. It reuses Iron Memory's lexical
tokenization and query-memory-style result envelope, as well as the existing
transient-memory secret-pattern screen. It does not call the shared-memory
writer, modify the VERIFIED lesson store, or promote a Notion Skill into a
repository-specific verified engineering skill.

Notion remains reference material. GitHub remains authoritative for current
repository policy, code, roadmaps and CI, while local worker receipts and owner
controls remain authoritative for execution.

## Caller contract

project_notion_page(page, block_list, *, allowed_page_ids, repository, retrieved_at)

- page: one REST page object with a UUID, exactly one title property and a
  timezone-qualified last_edited_time.
- block_list: a complete Notion API list response; has_more must be false,
  next_cursor null, and every block a verified direct child of the page.
- allowed_page_ids: explicitly approved UUIDs. A database or workspace is not
  implicitly authorized in full. Private personal Memories are excluded by default.
- repository: the exact authorized repository (e.g. jweter/project-orchestrator).
  Cross-repository scope is never inferred.
- retrieved_at: an externally supplied timezone-qualified time; never a
  fabricated clock timestamp.
- Only basic text blocks are supported. Media, tables, files, nested blocks,
  linked pages, incomplete pagination and unknown types fail closed.
- Text is capped at 16 KiB and the input at 100 blocks. Existing secret
  patterns add defense but do not replace upstream classification.
- Canonical source URL is derived from the allowlisted UUID; untrusted page
  URLs are not followed.
- Projection fields include OBSERVED / UNREVIEWED / ADVISORY status, source
  revision, block IDs, repository scope, retrieval timestamp, and a deterministic
  integrity digest. A digest is not an authentication signature.

query_notion_context(records, *, repository, terms, limit=8)

This returns state, query, results and authority_note, analogous to the
existing Iron Memory lookup interface. Results include score, match_reasons
and context, and can never become VERIFIED lessons or Product Reality evidence.

## Example (offline only)

~~~python
from project_orchestrator.notion_source_adapter import (
    project_notion_page,
    query_notion_context,
)

record = project_notion_page(
    page_fixture,
    complete_blocks_fixture,
    allowed_page_ids=["4269eaf5-47fa-81e6-8bf4-cc62c10d1ca1"],
    repository="jweter/project-orchestrator",
    retrieved_at="2026-11-28T15:00:00Z",
)
found = query_notion_context(
    [record], repository="jweter/project-orchestrator", terms=["roadmap"]
)
~~~

## Verification

~~~powershell
$env:PYTHONPATH = "$PWD\src"
python -m pytest -q tests/test_notion_source_adapter.py
python -m ruff check src/project_orchestrator/notion_source_adapter.py tests/test_notion_source_adapter.py
python engineering/preflight.py
~~~

The offline tests cover allowlist failures, removed pages, incomplete
pagination, nested or unsupported blocks, cross-page mixing, duplicate
data, large/secret content, untrusted prompt-like text, deterministic
search, cross-repository isolation, and digest tampering.

## N02+ boundary

A future separately authorized transport must implement Notion authentication,
permission scoping, complete pagination, retries, rate limits, revocation,
cache freshness and audit logging. A passing offline N01 test is not proof
that Iron God can contact Notion.
