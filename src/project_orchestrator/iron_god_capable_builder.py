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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .claude_dispatch import ROUTINES, dispatch_if_warranted, ingest_session_result, specialist_scorecard

DISPATCH_DIR = "capable-dispatches"
DEFAULT_DAILY_LIMIT = 2
MAX_ATTEMPTS = 2  # a request that two capable attempts failed needs a human decision, not a third try
STALE_SECONDS = 12 * 3600
# Observed sessions open their pull request within minutes (Project-Everward #343 and #348: about 2-3 minutes after
# dispatch). After QUIET_SECONDS with no pull request and no session-branch commit a dispatch is only SUSPECTED_STALE:
# silence is not proof of failure, so nothing is failed, retried or re-requested. It is CONFIRMED_FAILED only by an
# authoritative signal; today that is the STALE_SECONDS hard timeout (a future session-status source would add another).
QUIET_SECONDS = 3 * 3600
SUSPECTED_STALE = "SUSPECTED_STALE"
CONFIRMED_FAILED = "CONFIRMED_FAILED"
LATE_DELIVERY_WINDOW = 48 * 3600  # a failed no-PR dispatch is re-checked for a late pull request this long
ROUTINE = "portfolio_builder"


def dispatch_state(lane_root: Path) -> Path:
    return Path(lane_root) / DISPATCH_DIR


def _write_row(state_dir: Path, row: dict[str, Any]) -> None:
    (state_dir / f"{row['dispatch_id']}.json").write_text(json.dumps(row, sort_keys=True, indent=2), encoding="utf-8")


def _literal_prefix(pattern: str) -> tuple[str, ...]:
    parts: list[str] = []
    for part in str(pattern).replace(chr(92), "/").split("/"):
        if any(char in part for char in "*?["):
            break
        if part:
            parts.append(part)
    return tuple(parts)


def paths_overlap(left: list[str], right: list[str]) -> bool:
    """Conservative: two path scopes overlap unless their literal directory prefixes provably diverge."""
    for a in left:
        for b in right:
            pa, pb = _literal_prefix(a), _literal_prefix(b)
            size = min(len(pa), len(pb))
            if pa[:size] == pb[:size]:
                return True
    return False


def effective_scope(request: dict[str, Any]) -> list[str]:
    """Every file a session may touch: its allowed paths plus the capability graph the prompt permits it to edit."""
    graph = str(request.get("graph_path") or GRAPH_PATH)
    return [*(request.get("allowed_paths") or []), graph]


def releases_repository_for(row: dict[str, Any], request: dict[str, Any]) -> bool:
    """A SUSPECTED_STALE dispatch stops blocking unrelated work, never the same request or overlapping files.

    Fails closed: a row that does not record its allowed paths (older ledger rows) keeps blocking.
    """
    if row.get("suspicion") != SUSPECTED_STALE or request_of(row) == request.get("id"):
        return False
    held = row.get("allowed_paths")  # the dispatched request's effective scope, graph included
    wanted = effective_scope(request) if request.get("allowed_paths") else None
    return bool(held) and bool(wanted) and not paths_overlap(list(held), list(wanted))


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


NO_PR_NOTE = "STALE_NO_PULL_REQUEST_OBSERVED"
GRAPH_PATH = "engineering/capability-graph.json"
LOCAL_FOLLOW_ON_LIMIT = 3
NO_LOCAL_BUILDER_PREFIX = "NO_PYTHON_PACKAGE"  # the refill's reason for a repository the local builder cannot build
PLANNING_SLICE = "extend-capability-graph"     # the refill's planning task: it writes slices already, by its own rules


def local_slice_contract(graph_path: str) -> str:
    """What makes a graph slice buildable by the free local model, as one paragraph a capable session can follow.

    The capable builder has a small daily budget and the local 7B is idle whenever no slice is small enough for it
    (measured 2026-11-13: one of seven repositories had any). The 7B builds; it does not plan. So the stronger session
    states the rule and computes the examples, and the 7B writes only the code that satisfies them. Every clause is a
    measured failure from docs/LOCAL_WORK_BACKLOG.md.
    """
    return (
        "A local slice is ONE new pure function in a NEW module of an existing Python package. Required fields: state PLANNED; "
        "capability_level 1; an objective of at most 580 characters whose FIRST sentence is the exact rule ('Return ...') and "
        "which states every edge rule (empty input, case, rounding, boundaries, duplicates), background last; acceptance; "
        "acceptance_examples with at least three items {\"call\": \"{fn}(literal arguments)\", \"expected\": literal, "
        "\"why\": one sentence}, the expected values COMPUTED by running a reference implementation you then discard, covering "
        "the edges, never two answers for one call; no file, path, stream, clock, random or network input (pass the data "
        "itself); allowed_paths = the package pattern and the tests pattern; forbidden_paths = every forbidden path of this "
        "request; runner_requirements [\"code\", \"local\", \"offline\"]; depends_on to order slices that build on each other. "
        f"Append to {graph_path}; never edit or remove an existing slice.")


def local_follow_on(request: dict[str, Any], graph_path: str) -> str | None:
    """The instruction that turns one capable dispatch into work the local builder can do afterwards (None: not applicable)."""
    reason = str((request.get("route") or {}).get("reason") or "")
    if request.get("slice") == PLANNING_SLICE or reason.startswith(NO_LOCAL_BUILDER_PREFIX):
        return None
    return (
        "Leave work for the free local builder (it develops between your dispatches and is idle without slices of its size). "
        f"In this same pull request, also append up to {LOCAL_FOLLOW_ON_LIMIT} local slices to the capability this slice "
        "belongs to, placed after it, for pure logic that the roadmap's NEXT outcome needs and that you did not build here. "
        "Small pieces of a bigger feature count; work that serves no stated roadmap outcome does not, and adding none is the "
        "correct result when no such logic exists (say so in the pull request description). Do not build these slices "
        "yourself and do not weaken this slice to create them. " + local_slice_contract(graph_path))


def render_prompt(request: dict[str, Any], *, prior_no_pr: int = 0) -> str:
    """The self-contained task for the routine: the roadmap slice, its fences and how to deliver it.

    ``prior_no_pr`` is how many earlier attempts at this very request ended with no pull request. Such an outcome used to
    look like a plain failure and the retry repeated the identical prompt (Knowledge Engine core/web/ai, 2026-11-03: all
    three slices had already been merged by another agent, the sessions found nothing to open, and a second attempt spent
    more of the daily budget). Now "already delivered" has an observable outcome and a retry says what happened before.
    """
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
    graph_path = str(request.get("graph_path") or GRAPH_PATH)  # the repository's declared graph, carried by the refill
    lines.append(
        "Before building, check whether this slice is ALREADY delivered at the baseline: read the merged history, the files the "
        "objective names, and the tests that satisfy each acceptance criterion. Other agents merge work into these repositories "
        "and the graph can lag behind. If it is delivered, do not rebuild it: open ONE pull request whose title starts with "
        f"'Iron God [{request['id']}]' that changes only {graph_path} (permitted for this purpose even where it is outside the "
        "allowed paths), setting this slice's state to VERIFIED_COMPLETE and adding a verification list that cites the merged "
        "pull requests, their merge commits, and the files and tests that satisfy each acceptance criterion. A session that "
        "opens no pull request is recorded as a failed attempt and spends the daily budget.")
    if prior_no_pr:
        lines.append(
            f"Earlier attempt(s) at this request ({prior_no_pr}) ended with no pull request. Unless there is a genuine blocker, "
            "that usually means the slice was already delivered: verify that first and record it with the evidence pull request above.")
    follow_on = local_follow_on(request, graph_path)
    if follow_on:
        lines.append(follow_on)
    lines += [
        "Delivery: implement exactly this slice, with repository-native tests, and open ONE pull request whose title "
        f"starts with 'Iron God [{request['id']}]'. Run the repository's canonical preflight before opening it. "
        "Do not merge, do not change branch protection, workflows, owner controls or secrets, and do not widen scope. "
        "If the slice is materially ambiguous or cannot be done within the allowed paths, open no pull request and "
        "report the exact question or blocker instead."]
    return "\n".join(lines)


def no_pr_attempts(state_dir: Path, request_id: str) -> int:
    return sum(1 for row in _rows(state_dir) if request_of(row) == request_id and row.get("notes") == NO_PR_NOTE)


def attempts_for(state_dir: Path, request_id: str) -> int:
    return sum(1 for row in _rows(state_dir) if str(row.get("task_key", "")).split("#a")[0] == request_id)


def spent_request_ids(lane_root: Path) -> set[str]:
    """Request ids whose capable attempts are used up: they need a human decision, not another try."""
    counts: dict[str, int] = {}
    for row in _rows(dispatch_state(lane_root)):
        key = str(row.get("task_key", "")).split("#a")[0]
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count >= MAX_ATTEMPTS}


def decide(request: dict[str, Any], *, lane_root: Path, enabled: bool, daily_limit: int = DEFAULT_DAILY_LIMIT,
           now: float | None = None, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Whether this request may be dispatched right now, and if not exactly why (no side effects)."""
    now = time.time() if now is None else now
    environ = os.environ if environ is None else environ
    route = request.get("route") or {}
    if route.get("builder") != "CAPABLE":
        return {"dispatch": False, "reason": "NOT_ROUTED_TO_CAPABLE_BUILDER"}
    if request.get("reserved_conflicts"):
        # An open pull request already owns files this slice may touch; wait for it (this is not an attempt).
        return {"dispatch": False, "reason": "BLOCKED_CONFLICT", "files": list(request["reserved_conflicts"])}
    if not enabled:
        return {"dispatch": False, "reason": "CAPABLE_BUILDER_DISABLED"}
    token_env = ROUTINES[ROUTINE][1]
    if not environ.get(token_env):
        return {"dispatch": False, "reason": "CAPABLE_BUILDER_UNCONFIGURED", "needs": token_env}
    state_dir = dispatch_state(lane_root)
    rows = _rows(state_dir)
    if attempts_for(state_dir, request["id"]) >= MAX_ATTEMPTS:
        return {"dispatch": False, "reason": "ATTEMPTS_EXHAUSTED"}
    if request["id"] in open_request_ids(lane_root, now=now) or request["id"] in merged_request_ids(lane_root):
        return {"dispatch": False, "reason": "REQUEST_ALREADY_DELIVERED_OR_PENDING"}
    holders = [r for r in rows if r.get("repository") == request["repository"] and r.get("status") == "DISPATCHED"]
    if any(not releases_repository_for(r, request) for r in holders):
        return {"dispatch": False, "reason": "REPOSITORY_HAS_OPEN_DISPATCH",
                "holders": [{"dispatch_id": r["dispatch_id"], "session_url": r.get("session_url"),
                             "state": r.get("suspicion") or "ACTIVE"} for r in holders]}
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
            "prompt": render_prompt(request, prior_no_pr=no_pr_attempts(state_dir, request["id"])),
            "local_capability_verified": False}
    result = dispatch_if_warranted(task, state_dir, scorecard=specialist_scorecard(state_dir), opener=opener)
    if not result.get("dispatched"):
        return {"dispatched": False, "reason": "ROUTINE_DEMOTED", "detail": result.get("reason")}
    try:  # the scope lets a later SUSPECTED_STALE state release only unrelated work; absent scope keeps blocking
        row_path = state_dir / f"{result['dispatch_id']}.json"
        row = json.loads(row_path.read_text(encoding="utf-8"))
        row["allowed_paths"] = effective_scope(request) if request.get("allowed_paths") else []
        _write_row(state_dir, row)
    except (OSError, ValueError, KeyError):
        pass
    return {"dispatched": True, "reason": "DISPATCHED", "dispatch_id": result["dispatch_id"],
            "session_id": result["session_id"], "task_key": task["task_key"]}


def request_of(row: dict[str, Any]) -> str:
    return str(row.get("task_key", "")).split("#a")[0]


def open_request_ids(lane_root: Path, *, now: float | None = None) -> set[str]:
    """Request ids still being delivered: a live session, or an observed pull request that is not yet merged or closed.

    The refill treats them as pending and never re-requests them, so an open pull request keeps exactly one owner.
    """
    now = time.time() if now is None else now
    pending = set()
    for row in _rows(dispatch_state(lane_root)):
        live = row.get("status") == "DISPATCHED" and now - float(row.get("started_at", 0)) < STALE_SECONDS
        delivering = row.get("outcome_kind") == "PRODUCT_PR" and row.get("pr_state") in (None, "OPEN")
        if live or delivering:
            pending.add(request_of(row))
    return pending


def merged_request_ids(lane_root: Path) -> set[str]:
    """Requests whose capable-builder pull request was merged: the slice is complete, not merely delivered."""
    return {request_of(row) for row in _rows(dispatch_state(lane_root)) if row.get("pr_state") == "MERGED"}


def pull_request_state(url: str) -> str | None:
    """OPEN, MERGED or CLOSED for a pull request URL, or None when GitHub cannot be asked."""
    out = subprocess.run(["gh", "pr", "view", url, "--json", "state", "--jq", ".state"], capture_output=True,
                         text=True, check=False, timeout=60)
    value = out.stdout.strip()
    return value if out.returncode == 0 and value in {"OPEN", "MERGED", "CLOSED"} else None


class LookupUnavailable(RuntimeError):
    """GitHub could not be asked about a request's pull request: that is unknown, never proof that none exists."""


def find_pull_request(repository: str, request_id: str) -> str | None:
    """URL of a pull request titled for this request in the product repository, via the owner's gh login.

    None means GitHub answered and no such pull request exists; ``LookupUnavailable`` means it could not be asked.
    """
    try:
        out = subprocess.run(["gh", "pr", "list", "--repo", repository, "--state", "all", "--search",
                              f"Iron God [{request_id}] in:title", "--json", "url,title", "--limit", "5"],
                             capture_output=True, text=True, check=False, timeout=60)
        rows = json.loads(out.stdout or "[]") if out.returncode == 0 else None
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise LookupUnavailable(type(exc).__name__) from exc
    if rows is None:
        raise LookupUnavailable(f"gh exit {out.returncode}")
    for row in rows:
        if str(row.get("title", "")).startswith(f"Iron God [{request_id}]"):
            return str(row["url"])
    return None


def _late_delivery(state_dir: Path, row: dict[str, Any], now: float,
                   lookup: Callable[[str, str], str | None]) -> dict[str, Any] | None:
    """A no-PR failure whose pull request appears afterwards is a delivery: record it so the slice is not retried."""
    if row.get("notes") != NO_PR_NOTE or row.get("pr_url") or now - float(row.get("started_at", 0)) > LATE_DELIVERY_WINDOW:
        return None
    try:
        url = lookup(row["repository"], request_of(row))
    except LookupUnavailable:
        return None
    if not url:
        return None
    ingest_session_result(state_dir, row["dispatch_id"], outcome_kind="PRODUCT_PR", pr_url=url,
                          product_advancement=True, notes="pull request observed on GitHub after the dispatch timed out")
    return {"dispatch_id": row["dispatch_id"], "id": request_of(row), "outcome": "PRODUCT_PR", "pr_url": url, "late": True}


def session_activity_since(repository: str, since: float) -> bool | None:
    """Whether any ``claude/*`` session branch in the repository has a commit at or after ``since``.

    True/False when GitHub answered, None when it could not be asked: an unknown is never evidence of abandonment.
    """
    refs = subprocess.run(["gh", "api", f"repos/{repository}/git/matching-refs/heads/claude/", "--jq", ".[].ref"],
                          capture_output=True, text=True, check=False, timeout=60)
    if refs.returncode != 0:
        return None
    cutoff = datetime.fromtimestamp(since, tz=timezone.utc)
    for ref in refs.stdout.split():
        branch = ref.removeprefix("refs/heads/")
        out = subprocess.run(["gh", "api", f"repos/{repository}/commits/{branch}", "--jq", ".commit.committer.date"],
                             capture_output=True, text=True, check=False, timeout=60)
        if out.returncode != 0:
            return None
        try:
            when = datetime.fromisoformat(out.stdout.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if when >= cutoff:
            return True
    return False


def reconcile(lane_root: Path, *, now: float | None = None,
              lookup: Callable[[str, str], str | None] = find_pull_request,
              state_of: Callable[[str], str | None] = pull_request_state,
              activity: Callable[[str, float], bool | None] = session_activity_since) -> list[dict[str, Any]]:
    """Fold observed outcomes into the existing measurement ledger; returns what changed.

    Also follows each observed pull request to its end (OPEN -> MERGED or CLOSED) so delivery stays pending while it
    is open and a merge counts as completion.
    """
    now = time.time() if now is None else now
    state_dir = dispatch_state(lane_root)
    changed = []
    for row in _rows(state_dir):
        if row.get("outcome_kind") == "PRODUCT_PR" and row.get("pr_state") not in ("MERGED", "CLOSED") and row.get("pr_url"):
            observed = state_of(str(row["pr_url"]))
            if observed and observed != row.get("pr_state"):
                row["pr_state"] = observed
                (state_dir / f"{row['dispatch_id']}.json").write_text(json.dumps(row, sort_keys=True, indent=2),
                                                                      encoding="utf-8")
                changed.append({"dispatch_id": row["dispatch_id"], "id": request_of(row), "pr_state": observed})
            continue
        if row.get("status") != "DISPATCHED":
            late = _late_delivery(state_dir, row, now, lookup)
            if late:
                changed.append(late)
            continue
        request_id = request_of(row)
        age = now - float(row.get("started_at", 0))
        try:
            url = lookup(row["repository"], request_id)
        except LookupUnavailable:
            continue  # unknown is not absent: keep the dispatch until GitHub can answer (never infer missing state)
        if url:
            ingest_session_result(state_dir, row["dispatch_id"], outcome_kind="PRODUCT_PR", pr_url=url,
                                  product_advancement=True, notes="pull request observed on GitHub")
            changed.append({"dispatch_id": row["dispatch_id"], "id": request_id, "outcome": "PRODUCT_PR", "pr_url": url})
        elif age >= STALE_SECONDS:
            done = ingest_session_result(state_dir, row["dispatch_id"], outcome_kind="FAIL", notes=NO_PR_NOTE)
            done.update(suspicion=None, confirmation=CONFIRMED_FAILED, confirmation_basis="STALE_TIMEOUT")
            _write_row(state_dir, done)
            changed.append({"dispatch_id": row["dispatch_id"], "id": request_id, "outcome": "FAIL",
                            "confirmation": CONFIRMED_FAILED})
        elif age >= QUIET_SECONDS:
            seen = activity(row["repository"], float(row.get("started_at", 0)))
            if seen is False and row.get("suspicion") != SUSPECTED_STALE:
                row.update(suspicion=SUSPECTED_STALE, suspected_at=now,
                           suspicion_basis="no pull request and no claude/* branch commit since dispatch")
                _write_row(state_dir, row)
                changed.append({"dispatch_id": row["dispatch_id"], "id": request_id, "suspicion": SUSPECTED_STALE})
            elif seen is True and row.get("suspicion") == SUSPECTED_STALE:
                row.update(suspicion=None, suspicion_cleared_at=now)  # activity reappeared: it is alive after all
                _write_row(state_dir, row)
                changed.append({"dispatch_id": row["dispatch_id"], "id": request_id, "suspicion": None})
    return changed
