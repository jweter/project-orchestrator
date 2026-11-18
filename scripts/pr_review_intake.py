"""Fail-closed, advisory intake of reviewer findings for existing Iron God PRs.

Invoked by the existing independent PR controller, never a new worker or owner
controller. Intake does not grant an attempt, edit product code, or resolve a
thread. A distinct authorized product-lane repair remains necessary.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from scripts import green_pr_merger

SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_FINDINGS = 30


def observe(repo: str, pr: dict[str, Any], state_root: Path,
            *, fetch_threads: Callable[[str, int], list[dict[str, Any]]] | None = None) -> dict[str, Any] | None:
    """Record an exact-head review finding once; never turn it into authority."""
    if (pr.get("author") or {}).get("login") != "jweter":
        return None
    if not str(pr.get("title") or "").startswith("Iron God ["):
        return None
    head, number = pr.get("headRefOid"), pr.get("number")
    if not isinstance(head, str) or not SHA.fullmatch(head) or type(number) is not int or number <= 0:
        return {"repository": repo, "pr": number, "state": "REVIEW_INTAKE_IDENTITY_UNVERIFIED"}
    try:
        threads = (fetch_threads or green_pr_merger.review_threads)(repo, number)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError):
        return {"repository": repo, "pr": number, "head": head, "state": "REVIEW_INTAKE_EVIDENCE_UNVERIFIED"}
    if not isinstance(threads, list) or len(threads) > 100:
        return {"repository": repo, "pr": number, "head": head, "state": "REVIEW_INTAKE_EVIDENCE_UNVERIFIED"}

    findings = []
    for t in threads:
        if not isinstance(t, dict) or type(t.get("resolved")) is not bool:
            return {"repository": repo, "pr": number, "head": head, "state": "REVIEW_INTAKE_EVIDENCE_UNVERIFIED"}
        if t["resolved"]:
            continue
        comments = t.get("comments")
        if not isinstance(comments, list):
            return {"repository": repo, "pr": number, "head": head, "state": "REVIEW_INTAKE_EVIDENCE_UNVERIFIED"}
        relevant = [v for v in comments if isinstance(v, dict) and
                    v.get("commit") == head and green_pr_merger.BLOCKING_RE.search(str(v.get("body") or ""))]
        if not relevant:
            continue
        path = t.get("path")
        thread_id = t.get("id")
        if not isinstance(thread_id, str) or not thread_id or not isinstance(path, str) or not path:
            return {"repository": repo, "pr": number, "head": head, "state": "REVIEW_INTAKE_EVIDENCE_UNVERIFIED"}
        findings.append({"thread_id": thread_id, "path": path, "line": t.get("line"),
                         "comment_digest": hashlib.sha256(
                             "\n".join(str(v.get("body") or "") for v in relevant).encode("utf-8")
                         ).hexdigest()})
    path = Path(state_root) / "pr-review-intake" / (repo.replace("/", "--") + f"--{number}.json")
    if not findings and not path.is_file():
        return None
    if len(findings) > MAX_FINDINGS:
        return {"repository": repo, "pr": number, "head": head, "state": "REVIEW_INTAKE_TOO_MANY_FINDINGS"}
    findings.sort(key=lambda v: (v["path"], v["thread_id"]))
    fingerprint = hashlib.sha256(json.dumps(
        [repo, number, head, findings], sort_keys=True).encode("utf-8")).hexdigest()
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("fingerprint") == fingerprint:
                return {"repository": repo, "pr": number, "head": head, "state": "REVIEW_INTAKE_UNCHANGED"}
        except (OSError, ValueError, AttributeError):
            return {"repository": repo, "pr": number, "head": head, "state": "REVIEW_INTAKE_RECORD_UNVERIFIED"}
    record = {"schema_version": 1,
              "status": "REVIEW_SCOPE_VALIDATION_REQUIRED" if findings else "NO_CURRENT_HEAD_FINDINGS",
              "repository": repo, "pr": number, "head_sha": head,
              "observed_at_utc": datetime.now(UTC).isoformat(),
              "findings": findings, "fingerprint": fingerprint,
              "advisory_only": True, "dispatchable": False,
              "attempt_budget_reset": False, "requires_current_owner_roadmap_scope": True,
              "instruction": "Re-fetch exact-head threads. Route to existing authorized product lane only; no new worker, grant or budget."}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {"repository": repo, "pr": number, "head": head,
            "state": "REVIEW_INTAKE_RECORDED" if findings else "REVIEW_INTAKE_NO_CURRENT_HEAD_FINDINGS",
            "finding_count": len(findings)}
