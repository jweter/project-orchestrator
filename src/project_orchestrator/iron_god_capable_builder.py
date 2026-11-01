"""Hand roadmap requests the local builder cannot take to a stronger builder, within a budget (issue #594).

The roadmap refill (``iron_god_roadmap_refill``) routes each request by measured capability. A request routed
``CAPABLE`` needs a stronger implementer than the local 7B. Iron God already has one mechanism for that:
``claude_dispatch`` fires a Claude Code cloud routine (``portfolio_builder``) with a task prompt, keeps a
duplicate-dispatch lease per task and measures outcomes in a scorecard. This module only adapts a request to it:

* the routine implements the slice and opens an ordinary pull request in the product repository, so the work
  meets that repository's own CI and review - nothing the routine writes ever becomes engine input directly;
* dispatch is OFF unless the owner enables ``portfolio_rotation.capable_builder`` *and* the routine's token is
  configured; a missing token records a blocker and fires nothing;
* a daily budget, one open dispatch per repository and a per-request attempt cap bound the usage, and the
  specialist scorecard can demote a routine that drifts from its role;
* a request routed ``NONE`` (no builder exists for the repository) is never dispatched.

Completion is observed from GitHub (a pull request whose title names the request id) and fed to the existing
``ingest_session_result`` measurements; a dispatch with no pull request after ``STALE_SECONDS`` is recorded as a
failure so the attempt cap, not a hung session, ends the attempt.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .claude_dispatch import ROUTINES, dispatch_if_warranted, ingest_session_result, specialist_scorecard

DISPATCH_DIR = "capable-dispatches"
DEFAULT_DAILY_LIMIT = 2
MAX_ATTEMPTS = 2  # a request that two capable attempts failed needs a human decision, not a third try
STALE_SECONDS = 12 * 3600
ROUTINE = "portfolio_builder"


def dispatch_state(lane_root: Path) -> Path:
    return Path(lane_root) / DISPATCH_DIR


def _rows(state_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(state_dir.glob("*.json")) if state_dir.is_dir() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def render_prompt(request: dict[str, Any]) -> str:
    """The self-contained task for the routine: the roadmap slice, its fences and how to deliver it."""
    lines = [
        f"Iron God roadmap slice {request['id']} for {request['repository']} (baseline {request['exact_head']}).",
        f"Roadmap traceability: {request['roadmap_ref']}",
        f"Objective: {request['objective']}",
        "Acceptance criteria:", *[f"- {item}" for item in request.get("slice_acceptance", [])],
        f"Allowed paths: {', '.join(request['allowed_paths'])}",
        f"Forbidden paths (never touch): {', '.join(request['forbidden_paths']) or 'none'}",
    ]
    if request.get("prior_failures"):
        lines.append("Earlier attempts failed - do not repeat them: " + json.dumps(request["prior_failures"]))
    if request.get("development_rules"):
        lines.append("Verified development rules: " + json.dumps(request["development_rules"])[:1500])
    lines += [
        "Delivery: implement exactly this slice, with repository-native tests, and open ONE pull request whose title "
        f"starts with 'Iron God [{request['id']}]'. Run the repository's canonical preflight before opening it. "
        "Do not merge, do not change branch protection, workflows, owner controls or secrets, and do not widen scope. "
        "If the slice is materially ambiguous or cannot be done within the allowed paths, open no pull request and "
        "report the exact question or blocker instead."]
    return "\n".join(lines)


def attempts_for(state_dir: Path, request_id: str) -> int:
    return sum(1 for row in _rows(state_dir) if str(row.get("task_key", "")).split("#a")[0] == request_id)


def decide(request: dict[str, Any], *, lane_root: Path, enabled: bool, daily_limit: int = DEFAULT_DAILY_LIMIT,
           now: float | None = None, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Whether this request may be dispatched right now, and if not exactly why (no side effects)."""
    now = time.time() if now is None else now
    environ = os.environ if environ is None else environ
    route = request.get("route") or {}
    if route.get("builder") != "CAPABLE":
        return {"dispatch": False, "reason": "NOT_ROUTED_TO_CAPABLE_BUILDER"}
    if not enabled:
        return {"dispatch": False, "reason": "CAPABLE_BUILDER_DISABLED"}
    token_env = ROUTINES[ROUTINE][1]
    if not environ.get(token_env):
        return {"dispatch": False, "reason": "CAPABLE_BUILDER_UNCONFIGURED", "needs": token_env}
    state_dir = dispatch_state(lane_root)
    rows = _rows(state_dir)
    if attempts_for(state_dir, request["id"]) >= MAX_ATTEMPTS:
        return {"dispatch": False, "reason": "ATTEMPTS_EXHAUSTED"}
    if any(r.get("repository") == request["repository"] and r.get("status") == "DISPATCHED" for r in rows):
        return {"dispatch": False, "reason": "REPOSITORY_HAS_OPEN_DISPATCH"}
    if sum(1 for r in rows if now - float(r.get("started_at", 0)) < 24 * 3600) >= daily_limit:
        return {"dispatch": False, "reason": "DAILY_BUDGET_EXHAUSTED"}
    return {"dispatch": True, "reason": "WITHIN_BUDGET"}


def dispatch_request(request: dict[str, Any], *, lane_root: Path, enabled: bool, daily_limit: int = DEFAULT_DAILY_LIMIT,
                     now: float | None = None, environ: dict[str, str] | None = None,
                     opener: Callable = urllib.request.urlopen) -> dict[str, Any]:
    decision = decide(request, lane_root=lane_root, enabled=enabled, daily_limit=daily_limit, now=now, environ=environ)
    if not decision["dispatch"]:
        return {"dispatched": False, **decision}
    state_dir = dispatch_state(lane_root)
    task = {"kind": "PRODUCT_SLICE", "repository": request["repository"],
            "task_key": f"{request['id']}#a{attempts_for(state_dir, request['id'])}",
            "prompt": render_prompt(request), "local_capability_verified": False}
    result = dispatch_if_warranted(task, state_dir, scorecard=specialist_scorecard(state_dir), opener=opener)
    if not result.get("dispatched"):
        return {"dispatched": False, "reason": "ROUTINE_DEMOTED", "detail": result.get("reason")}
    return {"dispatched": True, "reason": "DISPATCHED", "dispatch_id": result["dispatch_id"],
            "session_id": result["session_id"], "task_key": task["task_key"]}


def open_request_ids(lane_root: Path, *, now: float | None = None) -> set[str]:
    """Request ids with a live capable dispatch; the refill treats them as pending, never re-requests them."""
    now = time.time() if now is None else now
    return {str(r.get("task_key", "")).split("#a")[0] for r in _rows(dispatch_state(lane_root))
            if r.get("status") == "DISPATCHED" and now - float(r.get("started_at", 0)) < STALE_SECONDS}


def find_pull_request(repository: str, request_id: str) -> str | None:
    """URL of a pull request titled for this request in the product repository, via the owner's gh login."""
    out = subprocess.run(["gh", "pr", "list", "--repo", repository, "--state", "all", "--search",
                          f"Iron God [{request_id}] in:title", "--json", "url,title", "--limit", "5"],
                         capture_output=True, text=True, check=False, timeout=60)
    if out.returncode != 0:
        return None
    for row in json.loads(out.stdout or "[]"):
        if str(row.get("title", "")).startswith(f"Iron God [{request_id}]"):
            return str(row["url"])
    return None


def reconcile(lane_root: Path, *, now: float | None = None,
              lookup: Callable[[str, str], str | None] = find_pull_request) -> list[dict[str, Any]]:
    """Fold observed outcomes into the existing measurement ledger; returns what changed."""
    now = time.time() if now is None else now
    state_dir = dispatch_state(lane_root)
    changed = []
    for row in _rows(state_dir):
        if row.get("status") != "DISPATCHED":
            continue
        request_id = str(row.get("task_key", "")).split("#a")[0]
        url = lookup(row["repository"], request_id)
        if url:
            ingest_session_result(state_dir, row["dispatch_id"], outcome_kind="PRODUCT_PR", pr_url=url,
                                  product_advancement=True, notes="pull request observed on GitHub")
            changed.append({"dispatch_id": row["dispatch_id"], "id": request_id, "outcome": "PRODUCT_PR", "pr_url": url})
        elif now - float(row.get("started_at", 0)) >= STALE_SECONDS:
            ingest_session_result(state_dir, row["dispatch_id"], outcome_kind="FAIL",
                                  notes="STALE_NO_PULL_REQUEST_OBSERVED")
            changed.append({"dispatch_id": row["dispatch_id"], "id": request_id, "outcome": "FAIL"})
    return changed
