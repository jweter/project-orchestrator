# N02 — Manual Notion Connection Manager (read-only, not activated)

**Status:** Staged code only. Depends on N01's pure advisory Notion adapter.
It does not automatically connect an active Iron God worker, alter schedules,
change Notion permissions, publish memory, or write to Notion.

## Windows user experience

Double-click scripts/iron_god_notion_connection_manager.cmd in a reviewed
checkout (Python 3.12+ required). A Tkinter window presents a masked token
input field (no PowerShell or command-line token); Test connection performs
GET of the bot identity and one approved page; Save securely is available
only after the exact token passes; Test saved credential explicitly decrypts
and retests. The application never logs or displays the token or HTTP bodies.

The API host is fixed to https://api.notion.com; it uses TLS validation,
GET only, no redirects, bounded responses, Notion-Version 2022-06-28,
and safe 401/403/404/429/TLS diagnostics.

## Data scope

Explicit allowlisted test page:
4269eaf5-47fa-8159-8302-fb53eb164372

Repository scope: jweter/project-orchestrator.
Do not grant access to the entire Memories database. Identity verification
alone does not prove page access; both checks must pass. N01 produces
OBSERVED / UNREVIEWED / ADVISORY context only. There is no promotion
to VERIFIED engineering memory, no owner-control modification, and no
automatic task context injection.

The token is stored only after an explicit click in a Windows user-scoped
DPAPI ciphertext file:
%LOCALAPPDATA%\ExponentialEngine\IronGodLocal\Integrations\notion-knowledge-bridge.dpapi

It is not committed, stored in SQLite, exported to Claude, or logged.
Revoke or rotate the token in Notion if compromised. The file is bound to
the Windows user account and is not reliably portable across machines.

## Verification and limitations

Run tests without credentials:

    python -m pytest -q tests/test_notion_n02_transport.py tests/test_notion_n02_secret_store.py
    python -m ruff check src/project_orchestrator/notion_n02_*.py tests/test_notion_n02_*.py
    python engineering/preflight.py

Windows CI must verify real DPAPI roundtrips. Test stubs verify
deterministic Notion API behavior without live network access.
No separate packaged executable is created; Python and Tkinter are required.

N03, if separately approved, can integrate Notion content with active
task retrieval. Saving a credential does not enable that capability.
