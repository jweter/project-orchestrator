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
_MAX_INBOX = 100
_MAX_REPORT_MESSAGES = 30


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


def _event_key(event: Mapping[str, str], recipient: str) -> str:
    """Same verified snapshot = same advisory message across cycles/restarts."""
    encoded = json.dumps({"event": event, "recipient": recipient}, sort_keys=True)
    return "engine-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _publish_lane_events(db: Path, state_root: Path) -> list[dict[str, Any]]:
    observed: list[dict[str, Any]] = []
    for source, path in (
        ("product", state_root / "iron-god-products" / "lane-status.json"),
        ("canon", state_root / "iron-god-canon" / "lane-status.json"),
    ):
        event = _lane_event(source, path)
        if event is None:
            continue
        content = json.dumps({**event,
                              "trust": "UNVERIFIED_REPORTED_LANE_STATUS",
                              "next": "Check canonical state and exact GitHub evidence"},
                             sort_keys=True)
        reference = event["packet_id"][:120] if event["packet_id"] else ""
        for recipient in _RECIPIENTS:
            result = mcp_coordination.post_message(
                db, sender_label="iron-god", recipient=recipient,
                kind="status", body=content,
                reference=reference,
                idempotency_key=_event_key(event, recipient),
            )
            observed.append({
                "source": source, "recipient": recipient,
                "message_id": result["message_id"],
                "replayed": bool(result["replayed"]),
            })
    return observed


def _read_inbox(db: Path) -> dict[str, Any]:
    """Report messages WITHOUT acking them or routing body into a builder."""
    cursor = 0
    unacknowledged = 0
    observed = 0
    envelopes: list[dict[str, Any]] = []
    while observed < _MAX_INBOX:
        page_size = min(30, _MAX_INBOX - observed)
        page = mcp_coordination.list_messages(
            db, recipient_label="iron-god", after_seq=cursor,
            limit=page_size,
        )
        messages = page["messages"]
        if not messages:
            return {"status": page["status"], "observed": observed,
                    "unacknowledged": unacknowledged, "messages": envelopes,
                    "has_more": False, "next_seq": cursor}
        for message in messages:
            observed += 1
            if not message["acknowledged"]:
                unacknowledged += 1
                if len(envelopes) < _MAX_REPORT_MESSAGES:
                    # The engine does not ingest untrusted free-text as a work
                    # directive; the MCP CLI remains available to retrieve it.
                    envelopes.append({
                        "message_id": message["message_id"], "seq": message["seq"],
                        "sender_label": message["sender_label"],
                        "kind": message["kind"], "reference": message["reference"],
                        "trust": "UNTRUSTED_ADVISORY_NOT_AUTHORIZATION",
                    })
        cursor = page["next_seq"]
        if len(messages) < page_size:
            return {"status": page["status"], "observed": observed,
                    "unacknowledged": unacknowledged, "messages": envelopes,
                    "has_more": False, "next_seq": cursor}
    return {"status": "TRUNCATED", "observed": observed,
            "unacknowledged": unacknowledged, "messages": envelopes,
            "has_more": True, "next_seq": cursor}


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
        inbox = _read_inbox(db)
        posted = _publish_lane_events(db, state_root) if publish else []
        result: dict[str, Any] = {
            "schema_version": 1,
            "status": "OBSERVED",
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "pending_unacknowledged": inbox["unacknowledged"],
            "last_message_seq": inbox["next_seq"],
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
