# Capable-builder session status: what can be known, and from where

Status: investigation, 2026-11-05. Documentation only; no behaviour depends on this file.

## Problem

The engine fires a Claude Code cloud routine (`portfolio_builder`) and gets back only a session id and URL. It then has
to decide whether that session is running, finished or lost. Today the only evidence is GitHub: an `Iron God [id]`
pull request (delivered), a `claude/*` session-branch commit (alive), or the 12 h hard timeout (confirmed failed).
Silence for `QUIET_SECONDS` is only `SUSPECTED_STALE` and never changes attempts or creates a retry.

## What Anthropic officially supports (read 2026-11-05)

| Source | What it gives | Fit |
|---|---|---|
| `POST /v1/claude_code/routines/{id}/fire` ([API reference](https://platform.claude.com/docs/en/api/claude-code/routines-fire)) | Starts a run; returns `claude_code_session_id` and `claude_code_session_url`. "It does not stream session output or wait for the session to complete." Experimental. | Dispatch only. |
| Routine bearer token | "One routine only; no read access." Not part of the Claude Platform API; no SDK; no token-management API. | Cannot be used to read status. |
| Routine run History in the web UI ([routines doc](https://code.claude.com/docs/en/routines)) | Each run ends in Error / Cancelled / neither; filterable as Failed / Cancelled / Succeeded. The doc notes this says how the session ended, not whether the work was completed. | Authoritative for "session ended and how", but human-facing. No documented HTTP read. |
| `/schedule list` and asking about run history in the Claude Code CLI (v2.1.227 or later) | Lists a scheduled task's recent runs with status and a link, and can read a run's log. | Official and scriptable only by running a Claude Code CLI turn, which spends the owner's Claude usage. |
| GitHub trigger on the routine | Starts sessions from repository events. | Does not report on sessions we fired. |

No documented, token-scoped HTTP endpoint returns a session's status. Third-party session APIs that turn up in searches
read local transcript files or are unofficial and are excluded (no unsupported endpoints, no browser automation).

## Options, in order of recommendation

1. **Make the session announce itself through GitHub (no new API, free).** Change the routine prompt so the first
   action is to push a branch `claude/<request id>` (or open a draft `Iron God [id]` pull request) and to update its
   body as it finishes (`DONE`, `ALREADY_DELIVERED`, `BLOCKED: <question>`). Observed sessions already open their PR
   within about 3 minutes, so this costs nothing and turns "never started" into a positive, early, attributable signal.
   `session_activity_since` would then look for that exact branch rather than any `claude/*` branch.
2. **Record the explicit end of a session, not only its first sign of life.** Ask the session to finish by setting a
   status on its pull request (a label or a final comment). Then `CONFIRMED_FAILED` can also come from a session that
   reported `BLOCKED`, and a long quiet run stays `ACTIVE` only while its branch advances.
3. **Owner-run status reconciliation through the CLI.** A scheduled, cheap `claude -p "/schedule list <routine>"` (or the
   `/schedule` run-history question) would return official run statuses. It costs usage, so it should be an opt-in
   fallback used only when a dispatch is `SUSPECTED_STALE`, not a poll. Needs an owner decision on spending.
4. **Wait for a supported read API.** The `/fire` reference is marked experimental and describes read access as absent.
   Revisit when a run-status endpoint or webhook is documented. The attestation point in `reconcile` (the place that
   turns a status into `CONFIRMED_FAILED`) is already isolated so a new source is one added branch.

Not recommended: driving claude.ai with browser automation, scraping session pages, or any endpoint that is not in the
official documentation.

## What the engine does meanwhile

- `ACTIVE`: no suspicion. `SUSPECTED_STALE`: no PR and no session-branch commit for 3 h. It blocks the same request and
  any overlapping files, never causes a retry, and spends no attempt. Unrelated, scoped work in the same repository may
  proceed. Rows without a recorded scope keep blocking.
- `CONFIRMED_FAILED`: only the 12 h hard timeout today, and it is the only state that spends an attempt.
- A pull request that appears after a no-PR failure (for 48 h) is recorded as a delivery, so the slice is not retried.
