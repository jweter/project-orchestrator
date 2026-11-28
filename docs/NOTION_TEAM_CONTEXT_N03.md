# N03 — Iron God Notion Documents access (live, read-only)

**Purpose:** After updating Iron God from GitHub, local team participants can
ask for current Notion Documents knowledge directly through the existing MCP
coordination server and CLI. There is no token copy/paste in PowerShell,
no new Notion integration, no new Notion page, and no additional permissions.

## Verified source and scope

- The user's existing **Iron God Knowledge Bridge** token was saved under
  Windows DPAPI by N02. The token is decrypted only within the local Python
  process, never printed, transmitted to MCP callers, or stored in Git.
- Authorized source: Notion **Documents** database only
  (ID: 4269eaf5-47fa-81d8-a7b3-eca9c5f2d2bd).
- The formerly hard-coded *Iron God multi-agent architecture* page belongs
  to private Memories and is deliberately **not** accessed. A 404 for that
  one page was not a connection failure.
- Source provenance, revision timestamp, content digest and clear trust labels
  from N01 are preserved. This is not VERIFIED engineering memory.

## Use after pulling the approved merged release

Manual local on-demand commands:

    project-orchestrator notion-team status
    project-orchestrator notion-team recent --limit 5
    project-orchestrator notion-team search --term architecture --limit 5

The existing local Iron God MCP server additionally exposes:

    get_notion_team_context(terms=["architecture"], limit=5)

Calls read Notion **live** (no periodic task, daemon, background polling or
SQLite mirroring). The tool can be invoked by any already authorized local
participant using the same MCP server and Windows user context. It returns
bounded excerpts with source title, last-edited time and document links.

Notion's database-query endpoint is an HTTP POST, but is **read-only** and is
the only allowed POST request. Individual page/blocks reads use GET. HTTPS
certificate validation and redirect denial remain enabled; no arbitrary
remote URL, workspace, database, page or repository can be selected by a
caller. Pagination, page count, timeouts and returned text are bounded.

If a page has unsupported nested blocks, excessive length or sensitive
content, N01 rejects the unsafe projection; N03 reports PARTIAL and a
skipped-document count rather than quietly claiming complete knowledge.
The read path never issues instructions or grants execution, approval,
owner control, merge authority, GitHub modification or Product Reality.

## Verification

On Azathoth with the local user's existing encrypted token, the read-only
status returned OK for one readable page in the Documents database.
The previous architecture page returned HTTP 404 because it is in private
Memories. N03 does not require that page.

These are **integration checks**, not proof that any agent has consumed the
returned context or complied with its information. The local MCP server must
be restarted/updated by the normal product update process before the new
tool is visible to already-connected clients. No production process is
restarted by this code change.

Run:

    python -m pytest -q tests/test_notion_source_adapter.py tests/test_notion_n02_transport.py tests/test_notion_n02_secret_store.py tests/test_notion_n02_connection_manager.py tests/test_notion_team_context.py tests/test_mcp_coordination.py tests/test_iron_god_mcp_protocol.py
    python engineering/preflight.py

This change does **not** create a new schedule or alter any existing DoWork
automation. To refresh the answer, call the tool or CLI again.
