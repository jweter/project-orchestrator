"""Best-effort, advisory-only mailbox integration for the resident Iron God loop.

Reads messages as UNTRUSTED ADVISORY data and emits bounded, factual lane-state
notifications. Never translates messages into grants, tasks, worker commands,
owner-control state, git operations or Product Reality evidence.

The adapter is fail-open for development: a missing/locked/corrupt mailbox
cannot block a supervisor iteration or create a competing runtime owner.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from . import mcp_coordination
from .state_io import atomic_json

_LANE_STATUSES = frozenset({
    "DISPATCHED", "BLOCKED", "REPAIR_PENDING", "REPAIRING",
    "PUBLISH_PENDING", "AWAITING_CI", "ACCEPTED_LOCAL", "MERGED", "FAILED",
    "IDLE_NO_APPROVED_BACKLOG_ITEM", "IDLE_NO_READY_SLICE",
    "ORPHANED_PRODUCT_WORKER_OWNERSHIP", "ACTIVE_WORKER_STATE_UNVERIFIED",
    "WORKER_LAUNCH_UNCONFIRMED",
})
# The lane adapter reads only a few fields from canonical state files. It
# NEVER forwards their arbitrary free-text error/details into assistant inboxes.
_TOKEN = re.compile(r"^[A-Za-z0-9._:/#@+-]{1,120}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,60}/[A-Za-z0-9_.-]{1,60}$")
_RECIPIENTS = ("claude-code", "chatgpt")
_PUBLICATION_STATES = frozenset({
    "PUBLISH_PENDING", "AWAITING_CI", "REPAIR_PENDING", "REPAIRING",
    "BLOCKED", "MERGED", "AWAITING_OWNER_REVIEW",
})
_SHA = re.compile(r"^[a-f0-9]{40,64}$")
_PACKET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_MAX_INBOX = 30
_MAX_REPORT_MESSAGES = 30
_MAX_PUBLICATIONS = 30


def _safe(value: Any, pattern: re.Pattern[str]) -> str:
    return value if isinstance(value, str) and pattern.fullmatch(value) else ""


def _mailbox_path(state_root: Path, local_appdata: str | None) -> Path | None:
    """Require the active installed state root, not a test or detached checkout."""
    if not local_appdata:
        return None
    base = Path(local_appdata)
    if not base.is_absolute():
        return None
    expected_state = base / "ExponentialEngine" / "flow-state"
    try:
        if Path(state_root).resolve() != expected_state.resolve():
            return None
    except OSError:
        return None
    return base / "ExponentialEngine" / "mailbox" / "coordination.sqlite"


def _lane_event(source: str, path: Path) -> dict[str, str] | None:
    try:
        if not path.is_file() or path.stat().st_size > 128_000:
            return None
        source_data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(source_data, dict):
        return None
    status = _safe(source_data.get("status"), _TOKEN)
    if status not in _LANE_STATUSES:
        return None
    repo = _safe(source_data.get("repository"), _REPO)
    packet = _safe(source_data.get("packet_id"), _TOKEN)
    worker = _safe(source_data.get("worker_status"), _TOKEN)
    # Do not expose arbitrary report data, grant tokens, paths or secrets.
    return {"source": source, "status": status, "repository": repo,
            "packet_id": packet, "worker_status": worker}


def _publication_events(path: Path, *, after_packet: str = "") -> tuple[list[dict[str, str]], str]:
    """Read canonical publication ledger, not a nested execution-lane summary.

    An item transition is independently keyed by packet/repo/PR/state/head.
    We report the recorded outcome, never assert that CI or merge was just
    independently verified by the communication layer.
    """
    try:
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return [], after_packet
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return [], after_packet
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
        return [], after_packet
    items = payload["items"]
    keys = sorted(k for k in items if isinstance(k, str) and _PACKET.fullmatch(k))
    # Bound the work per supervisor iteration, but preserve the exact cursor
    # so a long-lived publications ledger cannot hide newer PR outcomes.
    ahead = [k for k in keys if k > after_packet]
    selected = (ahead if ahead else keys)[:_MAX_PUBLICATIONS]
    events: list[dict[str, str]] = []
    for raw_packet in selected:
        item = items[raw_packet]
        if not isinstance(item, dict):
            continue
        status = _safe(item.get("state"), _TOKEN)
        packet = _safe(raw_packet, _PACKET)
        repository = _safe(item.get("repository"), _REPO)
        if status not in _PUBLICATION_STATES or not packet or not repository:
            continue
        pr = item.get("pr")
        number = pr.get("number") if isinstance(pr, dict) else None
        pr_number = str(number) if type(number) is int and 0 < number <= 999999999 else ""
        events.append({
            "source": "publication",
            "status": status,
            "packet_id": packet,
            "repository": repository,
            "pr_number": pr_number,
            "head_sha": _safe(item.get("head_sha"), _SHA),
            "merged_sha": _safe(item.get("merged_sha"), _SHA),
        })
    return events, (selected[-1] if selected else after_packet)


def _event_key(event: Mapping[str, str], recipient: str) -> str:
    """Same reported snapshot = same advisory message across cycles/restarts."""
    encoded = json.dumps({"event": event, "recipient": recipient}, sort_keys=True)
    return "engine-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _publish_lane_events(db: Path, state_root: Path, *,
                         publication_after: str = "") -> tuple[list[dict[str, Any]], str]:
    observed: list[dict[str, Any]] = []
    events: list[dict[str, str]] = []
    for source, path in (
        ("product", state_root / "iron-god-products" / "lane-status.json"),
        ("canon", state_root / "iron-god-canon" / "lane-status.json"),
    ):
        event = _lane_event(source, path)
        if event is not None:
            events.append(event)
    # Publication transitions are persisted in their own authoritative ledger.
    # Top-level lane status often still reads IMPLEMENTING or DISPATCHED
    # after an independent PR reaches AWAITING_CI or MERGED.
    publication_events, publication_cursor = _publication_events(
        state_root / "iron-god-products" / "publications.json",
        after_packet=publication_after,
    )
    events.extend(publication_events)
    for event in events:
        trust = ("UNVERIFIED_REPORTED_PUBLICATION_STATE" if event["source"] == "publication"
                 else "UNVERIFIED_REPORTED_LANE_STATUS")
        content = json.dumps({
            **event, "trust": trust,
            "next": "Check canonical state and exact GitHub evidence",
        }, sort_keys=True)
        reference = ("PR#" + event["pr_number"] if event.get("pr_number")
                     else event.get("packet_id", ""))[:120]
        for recipient in _RECIPIENTS:
            result = mcp_coordination.post_message(
                db, sender_label="iron-god", recipient=recipient,
                kind="status", body=content,
                reference=reference,
                idempotency_key=_event_key(event, recipient),
            )
            observed.append({
                "source": event["source"], "recipient": recipient,
                "message_id": result["message_id"],
                "replayed": bool(result["replayed"]),
            })
    return observed, publication_cursor


def _read_inbox(db: Path, *, after_seq: int = 0) -> dict[str, Any]:
    """Page pending-only messages, never converting their bodies to work."""
    page = mcp_coordination.list_pending_messages(
        db, recipient_label="iron-god", after_seq=after_seq, limit=_MAX_INBOX,
    )
    messages = page["messages"]
    # Deliberately omit untrusted message bodies from supervisor state.
    envelopes = [{
        "message_id": message["message_id"], "seq": message["seq"],
        "sender_label": message["sender_label"],
        "kind": message["kind"], "reference": message["reference"],
        "trust": "UNTRUSTED_ADVISORY_NOT_AUTHORIZATION",
    } for message in messages[:_MAX_REPORT_MESSAGES]]
    return {
        "status": page["status"],
        "observed": len(messages),
        "unacknowledged": page["pending_total"],
        "messages": envelopes,
        "has_more": page["pending_total"] > len(messages),
        "next_seq": page["next_seq"],
        "wrapped": page["wrapped"],
    }


def _inbox_cursor(state_root: Path) -> int:
    """Recover the last completed scan, not an unpersisted process cursor."""
    path = state_root / "iron-god-mailbox-observation.json"
    try:
        if not path.is_file() or path.stat().st_size > 128_000:
            return 0
        previous = json.loads(path.read_text(encoding="utf-8"))
        if previous.get("status") != "OBSERVED":
            return 0
        cursor = previous.get("last_message_seq")
        return cursor if type(cursor) is int and cursor >= 0 else 0
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def _publication_cursor(state_root: Path) -> str:
    """Resume bounded publication-ledger scanning after restart."""
    path = state_root / "iron-god-mailbox-observation.json"
    try:
        if not path.is_file() or path.stat().st_size > 128_000:
            return ""
        previous = json.loads(path.read_text(encoding="utf-8"))
        if previous.get("status") != "OBSERVED":
            return ""
        return _safe(previous.get("last_publication_packet"), _PACKET)
    except (OSError, ValueError, TypeError, AttributeError):
        return ""


def advisory_supervisor_pass(
    state_root: Path,
    *,
    local_appdata: str | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    """One bounded observation pass from an already-authorized supervisor loop.

    The function has no effect on control state, worker claims or owner gates.
    Publishing is optional for read-only diagnostics and tests. All failures
    return a status; communication must NEVER prevent product development.
    """
    state_root = Path(state_root)
    base = os.environ.get("LOCALAPPDATA") if local_appdata is None else local_appdata
    db = _mailbox_path(state_root, base)
    if db is None:
        return {"status": "SKIPPED_NOT_PINNED_STATE_ROOT"}
    if not db.is_file():
        return {"status": "MAILBOX_NOT_INITIALIZED"}
    try:
        inbox = _read_inbox(db, after_seq=_inbox_cursor(state_root))
        publication_cursor = _publication_cursor(state_root)
        if publish:
            posted, publication_cursor = _publish_lane_events(
                db, state_root, publication_after=publication_cursor,
            )
        else:
            posted = []
        result: dict[str, Any] = {
            "schema_version": 1,
            "status": "OBSERVED",
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "pending_unacknowledged": inbox["unacknowledged"],
            "last_message_seq": inbox["next_seq"],
            "last_publication_packet": publication_cursor,
            "inbox": inbox, "published": posted,
            "authority": "ADVISORY_ONLY_NO_EXECUTION_GRANTS",
        }
        # Recording observations is not an acknowledgement. The engine should
        # explicitly process a message before calling ack_message with its ID.
        atomic_json(state_root / "iron-god-mailbox-observation.json", result)
        return result
    except Exception as exc:
        # Do not write the exception text: external paths or secrets may appear.
        return {"status": "MAILBOX_UNAVAILABLE", "error_type": type(exc).__name__}
