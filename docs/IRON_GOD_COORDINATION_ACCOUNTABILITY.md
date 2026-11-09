# Coordination accountability — issue #675, incremental M4 protocol

**Status:** implementation proposal; uncontrolled until exact-head CI, independent review, merge, and owner-controlled release. This does not change the live supervisor, add a service, or launch a model.

## What this adds

The existing advisory mailbox reliably stores messages and recipient acknowledgments. An acknowledgment alone cannot establish that anyone accepted a task, supplied an answer, or delivered code. This slice provides **tracked handoffs** with a sender-visible lifecycle and a correlated recipient reply.

- One direct **request** is posted as an ordinary advisory `decision_request` with its own immutable mailbox `message_id`. It is separately indexed as a tracked request with a GitHub/issue reference and an optional UTC deadline.
- The **recipient**, in an already authorized working session, can record `ACCEPTED`, `WORKING`, `BLOCKED`, `NEEDS_OWNER`, `DECLINED`, or `COMPLETED`. The recipient's disposition creates an immutable record and a durable response message to the original sender containing `response_to: <original message ID>`.
- The original message is acknowledged **only after** that correlated reply has been persisted. After a crash, `repair-replies` safely resends only previously recorded responses, using stable idempotency keys. It does not decide work.
- A read-only **requests** snapshot exposes current disposition, age, deadline, acknowledgment, and whether the reply actually exists. No raw message bodies or secrets are printed in the snapshot.
- An overdue unanswered request is shown as `WAITING_FOR_AGENT`; this means *no timely disposition was recorded*, **not** proof that the model is running, offline, or ignoring the request.

The old mailbox remains compatible. Legacy ordinary `post` messages are not silently converted to tracked requests. The existing SQLite `user_version=1` and immutable message/receipt rules remain intact. Two additive tables are initialized only when the first tracked request is created; status and request-list reads never initialize the database.

## CLI after deployment

Existing fixed-principal wrapper:

```powershell
$s = 'C:\Users\jerem\Documents\IronGod-Reports\ig-mailbox.ps1'
powershell -NoProfile -File $s requests --role incoming
powershell -NoProfile -File $s requests --role outgoing
```

Send one explicit, tracked review assignment (use a stable application-level key to prevent duplicate requests):

```powershell
powershell -NoProfile -File $s handoff --to claude-code --body 'Review PR#680 at exact head' --reference PR#680 --key review-680-head-d829c0c
```

In an already-authorized Claude session, respond with the actual returned request ID:

```powershell
powershell -NoProfile -File $s disposition --id <message_id> --state ACCEPTED --key accept-review-680
powershell -NoProfile -File $s disposition --id <message_id> --state WORKING --key started-review-680
powershell -NoProfile -File $s disposition --id <message_id> --state COMPLETED --evidence PR#680 --key verified-review-680
```

`COMPLETED` here is explicitly **RECIPIENT_SELF_REPORTED** even when an evidence reference is present. A separate GitHub CI/review reader must independently verify it. The protocol itself cannot approve a PR or grant engineering authority.

If a process died after writing a disposition event but before sending its reply, the **same** configured recipient can recover it without a model call:

```powershell
powershell -NoProfile -File $s repair-replies --limit 10
```

All write commands require the existing local advisory-writes configuration; they fail closed without it. No remote identities are authenticated by mere labels, and the protocol must not be exposed to an untrusted public network.

## Lifecycle

```text
QUEUED -> ACCEPTED -> WORKING -> COMPLETED
             |           |
             +----------> BLOCKED / NEEDS_OWNER / DECLINED

QUEUED + deadline elapsed (or 24h without explicit deadline)
    => WAITING_FOR_AGENT (read-only derived diagnosis; request is not deleted)

Any persisted disposition before a reply is posted
    => reply_pending=true; repair-replies can replay it
```

The `DELIVERED_AWAITING_RESPONSE` view means the recipient has acknowledged the original message without a tracked disposition; it does **not** count as acceptance. Terminal states are not silently reopened by the next mailbox pass; a new authorized request is needed for new work.

## Deployment and acceptance limits

This PR is an **increment**, not automatic three-agent coordination by itself. Claude's separately owned M2 session hooks must use these or equivalent protocol operations at supported session boundaries. ChatGPT's existing hourly automation can use them when its connected CLI release supports them. The resident coordinator's automatic liveness/watchdog integration remains a follow-on: no new poller or model is started in this change.

Acceptance for this increment: tests prove a direct handoff, correlated reply, recipient-only disposition, no false ACK, read-only overdue state, crash recovery, immutable event history, principal filtering, idempotent retries, invalid-transition rejection, and backward compatibility with the v1 mailbox.

**Fail-closed boundaries:** no automatic paid Claude activation, no local builder launch, no unsolicited owner-control change, no unapproved release/merge, no external listener, no secret mirroring into GitHub, and no completion claims based solely on a mailbox receipt.
