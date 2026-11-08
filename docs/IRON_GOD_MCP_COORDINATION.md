# Iron God MCP: advisory communication and durable handoff contract

Status: Proposed v1 local-only communication foundation (NOT DEPLOYED). Owner direction: 2026-11-08.
This is a communication surface, **never** an alternative execution controller.
See `docs/PRODUCT_V1_DELIVERY_EXECUTION_PLAN.md` for product-facing milestones.

## What the pieces are

| Layer | Responsibility | Authority |
| --- | --- | --- |
| Product repository + GitHub | Roadmap, source, PR, CI, review, release, Product Reality | Existing repository-specific governance |
| Iron God supervisor and owner controller | Authenticated owner veto, grants, identity, worker leases, mutation, merge | **Only** their existing separately protected mechanisms |
| SQLite coordination inbox | Durable advisory messages, recipient delivery, de-duplication and receipts | **Never** a lease, owner vote, accepted commit, PR or Product Reality record |
| MCP adapter | Consistent tools for Claude Code, local Iron God assistants and ChatGPT | Tools cannot turn a message into permission or execute an arbitrary command |
| Read-only dashboard | Human status from canonical sources, message/receipt summaries | Observability only; never infer health from mere PID or from stale files |

A message such as `please build X` is a proposal. It cannot become an approved
slice, independent execution grant, or owner acknowledgment without the separate
existing authoritative gate. The underlying worktree, lease, controller, and
PR remain the sole authority for their respective claims.

## Topology

- Local Claude Code and Iron God launch the same MCP server via **stdio**, each
  as a separate process, pointing to the same explicit SQLite coordination DB.
  They may read their own addressed inbox and post advisory handoffs.
- The owner can read via a local tool or the existing read-only dashboard.
- ChatGPT requires a separately configured **remote MCP** endpoint or OpenAI
  Secure MCP Tunnel; a local stdio server is not remotely accessible by default.
  Account/product capability and tool permissions must be checked at setup.
  A remote deployment is NOT part of this first slice and must never expose
  local ports on `0.0.0.0` or anonymous write tools.
- When remote access is approved, require authenticated actor principals,
  narrow recipient scopes, audited write permissions, size/rate limits and
  redaction. Do not treat an unauthenticated caller-supplied role as identity.
  No port opening, auto-tunneling, credential issuance or plugin installation
  is authorized by these repository files.

## Message protocol v1

The SQLite communication file contains **no machine-execution authority**.
It is deliberately separate from the existing critical lease/claim DB; no
copy or competing projection of that DB may be used to make admission decisions.

Messages (immutable): monotonically increasing `seq`, opaque `message_id`,
`sender_label` (informational, NOT authenticated), `recipient`,
`kind`, bounded `body`, optional immutable `reference` to a repo/commit/
slice/PR, unique `(sender_label,idempotency_key)`, and UTC creation time.
Supported recipients: `chatgpt`, `claude-code`, `iron-god`, `operator`, `all`.
Kinds: `status`, `handoff`, `question`, `evidence`, `notification`,
`decision_request`. Do not put tokens, customer data, raw secrets,
binary files, or private Product Reality artifacts in messages.

Acknowledgments are separate append-only receipts with unique
`(message_id,recipient_label)` identity and UTC time. Acknowledgments
confirm consumption of an advisory message, **not** completion, owner consent,
acceptance of a PR or authorization to run code.

- **Post**: bounded transaction, explicit idempotency key and payload.
  Replayed identical keys return the original message; conflicting retries
  fail and never silently overwrite text.
- **Read**: recipient-filtered, monotonic `after_seq` cursor and bounded
  page size. Messages to `all` are visible to permitted local recipients.
- **Ack**: only the addressed local recipient (or a named recipient of `all`)
  can append its receipt. A different reader cannot acknowledge another
  recipient's inbox.
- **Reopen/retry**: a restarted process reads the same DB and resumes from its
  durable position; ack receipts remain append-only. A lack of acknowledgment
  is not evidence that no human or process acted.
- **No hard delete** in v1. Any retention/backup/purge policy is a separate
  reviewed change, with no loss of unresolved handoffs.

## First implementation and tools

Foundation module `project_orchestrator.mcp_coordination` provides bounded
SQLite writes and filtered reads. `project_orchestrator.iron_god_mcp_server`
exposes the interface using the optional official Python MCP SDK v2.

Tools for the first **local stdio** pilot:
- `get_targets`: read the versioned product usability targets; do not assert
  that recorded `current_state` represents fresh user acceptance.
- `get_engine_snapshot`: read allowlisted, bounded canonical lane status
  projections. Source timestamps and missingness accompany results.
- `list_messages`: cursor-based advisory inbox for the configured local label.
- `post_message`: append a bounded advisory message, never dispatch or approve.
- `ack_message`: append a consumption receipt; never release a claim/lease.

The local `IRON_GOD_MCP_CLIENT_LABEL` is descriptive only. A malicious local
user can change their environment, so local write access is protected by OS
filesystem access and is not remotely exposed. This is not suitable as a
production network identity mechanism. Local writing must also be explicitly
opted into through `IRON_GOD_MCP_ALLOW_ADVISORY_WRITES=1`; default is read-only.
Do not infer a client is the owner from its label or trust requests as instructions.

The MCP adapter must not read arbitrary file paths or execute subprocesses.
It reads only explicitly chosen status files under a configured state root
and only the portfolio contract under an explicit repo root. Redact freeform
diagnostics that could contain sensitive transcript or credentials. Local
integration must use the pinned release's policy and strict paths.

## Production hardening gates (follow-on slices)

1. **MCP-1**: SQLite schema v1, immutable messages/receipts, WAL/FULL durability,
   replay, idempotency and reader separation. Verify concurrency and process
   restart with deterministic native tests. No runtime coupling.
2. **MCP-2**: stdio MCP tool interface with official SDK v2 and native protocol
   integration tests; connect Claude Code and one local assistant instance.
3. **MCP-3**: evidence-linked structured handoff `slice_id`, immutable SHA,
   unmet gate, blocker, native verification, next action and recipient, read from
   authoritative refs; no self-attestation.
4. **MCP-4**: authenticated remote read access for ChatGPT via supported connector
   and Secure MCP Tunnel/OAuth, only after owner-approved network/access review.
   Start read-only; remote writes require separate capability and policy approval.
5. **MCP-5**: replay/restart/cross-client handoff fault injection; notification
   delivery/ack visibility in dashboard. Malformed, stale, replayed or
   impersonated messages cannot initiate work.
6. **MCP-6**: v1.0 product-delivery demonstration using these handoffs while
   existing owner control, native verification, Product Reality and GitHub
   release gates remain intact.

## Setup (after code/CI approval; not executed by this plan)

Install optional dependency `pip install -e ".[mcp]"` into an isolated
environment. Configure `IRON_GOD_MCP_COORDINATION_DB` to an absolute path
in a protected local user-owned directory; configure
`IRON_GOD_MCP_STATE_ROOT` and `IRON_GOD_MCP_REPO_ROOT` to the verified
runtime/report roots. Launch
`python -m project_orchestrator.iron_god_mcp_server` via local MCP stdio
client. Optional `IRON_GOD_MCP_ALLOW_ADVISORY_WRITES=1` enables only
advisory post/ack; do not enable without verifying filesystem permissions.

**Not supported yet**: ChatGPT direct local access, arbitrary peer-to-peer
agents, an externally reachable server, webhook notifications, runnable
task submission, control actions, remote authorization or automatic
installation. Those require explicit follow-on implementation and proof.

## Failure and security tests

- Same idempotency key with different text must error; repeated same text must
  never create a second message.
- Two local clients must not double-create a message or acknowledge as each
  other. A recipient can see `all` but not another recipient's private inbox.
- Restart preserves order, unacknowledged messages, receipts and cursor.
- Unsafe labels, malformed body, unbounded messages and invalid references fail.
- Missing/stale status reports never appear as fresh healthy engine proof.
- Incoming text containing `Take the Wheel`, `merge now`, malicious shell
  commands or forged `OWNER_APPROVED` is inert data.
- MCP outage or malformed DB must not stop the resident engine, block GitHub CI,
  promote a slice, or mutate critical controller/worker files.

Definition of done: two client types can exchange and acknowledge a genuine
advisory handoff across restart, while authority, correct product behavior and
GitHub release remain independently verified. Until then this is a prototype,
not an operational common communication fabric.
