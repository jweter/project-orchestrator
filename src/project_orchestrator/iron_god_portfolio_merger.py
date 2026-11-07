"""Autonomous portfolio merger: roadmap-development pull requests merge without a human approving each one.

The owner wrote the roadmaps and the standing authorization; approving every pull request that delivers them adds
nothing. What must never be skipped is verification, so this module makes the *policy* explicit, deterministic and
auditable, and leaves a durable record of every merge and every refusal:

* **Discover** open pull requests across the authorized repositories and recognise roadmap development by its
  ``Iron God [RM-...]`` identity, opened under the owner's own account. Anything else is ignored, and a dependency
  bot's pull request is escalated to the owner rather than merged.
* **Authorize** the change from the capability graph on the repository's ``main`` (never from the pull request, so a
  change cannot widen its own scope): the slice's ``allowed_paths`` and ``forbidden_paths`` must contain every changed
  file. A planning pull request may change only the graph, additively, and may not grant itself or later slices access
  to protected paths.
* **Read the diff** (not only the file list): protected and security-sensitive paths, dependency manifests, secret
  patterns, deleted tests, unreadable or oversized diffs.
* **Verify** exact-head canonical preflight and every required check, a ``CLEAN`` merge state, no unresolved review
  conversation, and the five-minute quiet window that restarts at promotion and at every new review activity.
* **Re-check** everything immediately before merging and squash-merge only the verified head (``--match-head-commit``);
  branch protection is never bypassed because the merge goes through GitHub's own gate.
* **Record** durable evidence for every merge, promotion and refusal in an append-only ledger.

``evaluate`` is a pure function of gathered facts. An optional advisor (a local model) may only *downgrade* a merge to an
escalation; no advisor can authorize one. Nothing here consults Claude or ChatGPT.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from .iron_god_capability_graph_parser import CapabilityGraphInvalidError, parse_capability_graph
from .iron_god_capable_builder import GRAPH_PATH
from .iron_god_roadmap_progress import TITLE_PREFIX as PROGRESS_TITLE_PREFIX
from .iron_god_roadmap_refill import DEFAULT_FORBIDDEN, PLANNING_SLICE, _matches, id_prefix, item_id
from .state_io import atomic_json

POLICY_VERSION = "portfolio-merger/1"

# The authorized repositories. tests/test_iron_god_portfolio_merger.py pins this to scripts/green_pr_merger.py.
PORTFOLIO_REPOS = (
    "jweter/project-orchestrator",
    "jweter/knowledge-engine-core",
    "jweter/knowledge-engine-web",
    "jweter/knowledge-engine-ai",
    "jweter/rocksmith-cdlc-generator",
    "jweter/Project-Everward",
    "jweter/VoiceStudio",
)
OWNER_LOGINS = frozenset({"jweter"})
REVIEW_SETTLE_SECONDS = 300
MAX_PULLS_PER_REPOSITORY = 100
MAX_FILES = 80
MAX_ADDED_LINES = 3000
PASS_CONCLUSIONS = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
PREFLIGHT_CHECK = re.compile(r"(?i)canonical[ -]preflight|full-preflight")

TAG = re.compile(r"^Iron God \[(RM-[A-Za-z0-9._-]+)\]")
PULL_URL = re.compile(r"https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/(\d+)")
SUFFIX = re.compile(r"-(?:r\d+|t\d+|cap)$")
PLAN_SUFFIX = re.compile(r"^[0-9a-f]{8}$")
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]+$|_test\.\w+$|\.test\.\w+$")

# Never authorized by a roadmap slice: they change how the engine, the repository or its secrets are governed.
PROTECTED_PATHS = (
    ".github/**", ".claude/**", "CODEOWNERS", "**/CODEOWNERS", "SECURITY.md", ".gitleaks*", "private/**",
    ".env", ".env.*", "**/.env", "**/.env.*", "**/*.pem", "**/*.key", "scripts/*merger*", "**/branch-protection*",
)
DEPENDENCY_FILES = (
    "pyproject.toml", "poetry.lock", "requirements*.txt", "Pipfile", "Pipfile.lock", "package.json",
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "vcpkg.json",
    "**/pyproject.toml", "**/requirements*.txt", "**/package.json", "**/package-lock.json", "**/Cargo.toml",
    "**/Cargo.lock", "**/go.mod", "**/go.sum", "**/vcpkg.json",
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"""(?i)(?:api[_-]?key|secret|token|passw(?:or)?d)\s*[:=]\s*['"][A-Za-z0-9/+_\-]{20,}['"]"""),
)
IMMUTABLE_SLICE_FIELDS = ("objective", "acceptance", "allowed_paths", "forbidden_paths", "capability_level",
                          "depends_on", "runner_requirements", "runtime_requirements")
APPEND_ONLY_FIELDS = ("verification",)

MERGE, PROMOTE, WAIT, REFUSE, ESCALATE, IGNORE = "MERGE", "PROMOTE", "WAIT", "REFUSE", "ESCALATE", "IGNORE"
AUTHORIZED = "AUTHORIZED"
ALWAYS_RECORDED = frozenset({"MERGED", "PROMOTED", "MERGE_FAILED", "PROMOTE_FAILED"})


@dataclass(frozen=True)
class Verdict:
    action: str
    reasons: tuple[str, ...]
    authorization: dict[str, Any]


# ---- small pure helpers --------------------------------------------------------------------------------------------


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _any_match(path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return _matches(path, list(patterns))


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def roadmap_identity(title: str) -> str | None:
    found = TAG.match(title or "")
    return found.group(1) if found else None


def identity_candidates(identity: str) -> list[str]:
    """The identity plus every form with its trailing -rN / -tN / -cap suffixes removed (revision, subtask, escalation)."""
    found, current = [identity], identity
    while SUFFIX.search(current):
        current = SUFFIX.sub("", current, count=1)
        found.append(current)
    return found


def resolve_authorization(repository: str, identity: str, graph: dict[str, Any]) -> dict[str, Any] | None:
    """The slice (or the planning grant) that authorizes this identity, from the graph on main; None if none does."""
    candidates = identity_candidates(identity)
    for capability in graph.get("capabilities") or []:
        if not isinstance(capability, dict):
            continue
        for row in capability.get("slices") or []:
            if isinstance(row, dict) and item_id(repository, str(capability.get("id")), str(row.get("id"))) in candidates:
                return {"kind": "slice", "slice": f"{capability.get('id')}/{row.get('id')}",
                        "allowed_paths": _strings(row.get("allowed_paths")),
                        "forbidden_paths": _strings(row.get("forbidden_paths"))}
    prefix = id_prefix(repository) + "plan-"
    for candidate in candidates:
        if candidate.startswith(prefix) and PLAN_SUFFIX.match(candidate[len(prefix):]):
            forbidden = {p for cap in graph.get("capabilities") or [] if isinstance(cap, dict)
                         for row in cap.get("slices") or [] if isinstance(row, dict)
                         for p in _strings(row.get("forbidden_paths"))} | set(DEFAULT_FORBIDDEN)
            return {"kind": "planning", "slice": PLANNING_SLICE, "allowed_paths": [GRAPH_PATH],
                    "forbidden_paths": sorted(forbidden)}
    return None


def graph_change_problems(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    """Reasons a graph edit is not a purely additive roadmap change. Empty means the edit is acceptable.

    The graph is the source of future authorization, so an edit may not rewrite what is already authorized, may not
    drop work or evidence, and may not grant a new slice access to protected paths or to the whole repository.
    """
    problems: list[str] = []
    base_caps = {c.get("id"): c for c in base.get("capabilities") or [] if isinstance(c, dict)}
    head_caps = {c.get("id"): c for c in head.get("capabilities") or [] if isinstance(c, dict)}
    for cap_id, cap in base_caps.items():
        new = head_caps.get(cap_id)
        if new is None:
            problems.append(f"CAPABILITY_REMOVED:{cap_id}")
            continue
        if cap.get("depends_on") != new.get("depends_on"):
            problems.append(f"CAPABILITY_DEPENDENCIES_EDITED:{cap_id}")  # changes which work becomes eligible
        old_ev, new_ev = _strings(cap.get("completion_evidence")), _strings(new.get("completion_evidence"))
        if new_ev[:len(old_ev)] != old_ev:
            problems.append(f"EVIDENCE_REWRITTEN:{cap_id}")
        old_ver, new_ver = cap.get("verification") or [], new.get("verification") or []
        if not isinstance(old_ver, list) or not isinstance(new_ver, list) or new_ver[:len(old_ver)] != old_ver:
            problems.append(f"EVIDENCE_REWRITTEN:{cap_id}.verification")
        if cap.get("state") != new.get("state") and new.get("state") not in {"VERIFIED_COMPLETE", cap.get("state")}:
            problems.append(f"CAPABILITY_STATE_REGRESSED:{cap_id}")
        old_rows = {r.get("id"): r for r in cap.get("slices") or [] if isinstance(r, dict)}
        new_rows = {r.get("id"): r for r in new.get("slices") or [] if isinstance(r, dict)}
        for slice_id, row in old_rows.items():
            changed = new_rows.get(slice_id)
            if changed is None:
                problems.append(f"SLICE_REMOVED:{cap_id}/{slice_id}")
                continue
            for field in IMMUTABLE_SLICE_FIELDS:
                if row.get(field) != changed.get(field):
                    problems.append(f"SLICE_AUTHORITY_EDITED:{cap_id}/{slice_id}.{field}")
            for field in APPEND_ONLY_FIELDS:
                old_value, new_value = row.get(field) or [], changed.get(field) or []
                if not isinstance(old_value, list) or not isinstance(new_value, list) or new_value[:len(old_value)] != old_value:
                    problems.append(f"SLICE_EVIDENCE_REWRITTEN:{cap_id}/{slice_id}.{field}")
            if row.get("state") != changed.get("state") and changed.get("state") not in {"VERIFIED_COMPLETE", row.get("state")}:
                problems.append(f"SLICE_STATE_REGRESSED:{cap_id}/{slice_id}")
    # a new slice inherits every fence the graph already declares (the engine's own planning contract)
    inherited = {p for cap in base_caps.values() for row in cap.get("slices") or [] if isinstance(row, dict)
                 for p in _strings(row.get("forbidden_paths"))}
    for cap_id, cap in head_caps.items():
        for row in cap.get("slices") or []:
            if not isinstance(row, dict) or row.get("id") in {r.get("id") for r in (base_caps.get(cap_id) or {}).get("slices") or [] if isinstance(r, dict)}:
                continue
            allowed = _strings(row.get("allowed_paths"))
            if not allowed:
                problems.append(f"NEW_SLICE_WITHOUT_SCOPE:{cap_id}/{row.get('id')}")
            for pattern in allowed:
                broad = pattern.strip() in {"", "*", "**", "**/*", "./**"}
                protected = any(_any_match(sample, [pattern]) for sample in PROTECTED_PATHS_SAMPLES)
                if broad or protected:
                    problems.append(f"NEW_SLICE_SCOPE_TOO_BROAD:{cap_id}/{row.get('id')}:{pattern}")
            if not inherited <= set(_strings(row.get("forbidden_paths"))):
                problems.append(f"NEW_SLICE_FORBIDDEN_NOT_INHERITED:{cap_id}/{row.get('id')}")
    return problems


# Concrete paths a slice scope pattern must never be able to match.
PROTECTED_PATHS_SAMPLES = (".github/workflows/ci.yml", ".claude/settings.json", "CODEOWNERS", "SECURITY.md",
                           "private/key", ".env", "config/.env.local", "certs/server.pem", "certs/server.key",
                           ".github/CODEOWNERS")


def is_progress_title(title: str) -> bool:
    return str(title or "").startswith(PROGRESS_TITLE_PREFIX)


def _slices(graph: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(str(c.get("id")), str(r.get("id"))): r for c in graph.get("capabilities") or [] if isinstance(c, dict)
            for r in c.get("slices") or [] if isinstance(r, dict)}


def new_evidence_urls(base: dict[str, Any], head: dict[str, Any]) -> dict[tuple[str, str], list[str]]:
    """For every slice a graph edit newly completes: the pull request URLs in the evidence it appended."""
    old = _slices(base)
    found: dict[tuple[str, str], list[str]] = {}
    for key, row in _slices(head).items():
        before = old.get(key)
        if before is None or before.get("state") == row.get("state") or row.get("state") != "VERIFIED_COMPLETE":
            continue
        added = [str(x) for x in (row.get("verification") or [])][len(before.get("verification") or []):]
        found[key] = sorted({f"https://github.com/{m.group(1)}/pull/{m.group(2)}" for m in PULL_URL.finditer(" ".join(added))})
    return found


def progress_problems(base: dict[str, Any], head: dict[str, Any], evidence: dict[str, str]) -> list[str]:
    """A roadmap-progress record may only mark delivered slices complete, citing pull requests GitHub reports MERGED."""
    problems: list[str] = []
    old_caps = {str(c.get("id")) for c in base.get("capabilities") or [] if isinstance(c, dict)}
    for cap in head.get("capabilities") or []:
        if isinstance(cap, dict) and str(cap.get("id")) not in old_caps:
            problems.append(f"PROGRESS_ADDS_CAPABILITY:{cap.get('id')}")
    for key in sorted(set(_slices(head)) - set(_slices(base))):
        problems.append(f"PROGRESS_ADDS_WORK:{key[0]}/{key[1]}")
    # the head must be the base with ONLY slice state/verification updated, as iron_god_roadmap_progress.apply_completions
    # does: a capability's own state or evidence, the roadmap, and every other field stay exactly as they were
    expected = copy.deepcopy(base)
    head_rows = _slices(head)
    for cap in expected.get("capabilities") or []:
        for row in cap.get("slices") or []:
            changed = head_rows.get((str(cap.get("id")), str(row.get("id"))))
            if changed is None:
                continue
            for field in ("state", "verification"):
                if field in changed:
                    row[field] = changed[field]
                else:
                    row.pop(field, None)
    base_caps = {str(c.get("id")): c for c in expected.get("capabilities") or [] if isinstance(c, dict)}
    for cap in head.get("capabilities") or []:
        before = base_caps.get(str(cap.get("id"))) if isinstance(cap, dict) else None
        if before is not None and {k: v for k, v in cap.items() if k != "slices"} != {k: v for k, v in before.items() if k != "slices"}:
            problems.append(f"PROGRESS_CHANGES_CAPABILITY:{cap.get('id')}")
    if expected != head and not problems:
        problems.append("PROGRESS_CHANGES_MORE_THAN_SLICE_COMPLETION")
    completed = new_evidence_urls(base, head)
    if not completed:
        problems.append("PROGRESS_COMPLETES_NOTHING")
    for key, urls in completed.items():
        if not urls:
            problems.append(f"PROGRESS_WITHOUT_EVIDENCE:{key[0]}/{key[1]}")
        for url in urls:
            if evidence.get(url) != "MERGED":
                problems.append(f"PROGRESS_EVIDENCE_NOT_MERGED:{url}")
    return problems


def diff_problems(files: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """(refusals, escalations) from reading the diff itself."""
    refuse: list[str] = []
    escalate: list[str] = []
    if not files:
        return ["EMPTY_DIFF"], []
    if len(files) > MAX_FILES:
        escalate.append(f"DIFF_TOO_MANY_FILES:{len(files)}")
    added_total = 0
    for row in files:
        path = str(row.get("path") or "")
        previous = str(row.get("previous_filename") or "")
        status = str(row.get("status") or "")
        added, deleted = int(row.get("additions") or 0), int(row.get("deletions") or 0)
        added_total += added
        patch = row.get("patch")
        for touched in sorted({path, previous} - {""}):  # a rename touches both its old and its new path
            if _any_match(touched, PROTECTED_PATHS):
                escalate.append(f"SECURITY_SENSITIVE_PATH:{touched}")
            if _any_match(touched, DEPENDENCY_FILES):
                escalate.append(f"DEPENDENCY_CHANGE_NEEDS_OWNER:{touched}")
        if status == "removed" and TEST_PATH.search(path):
            escalate.append(f"TEST_FILE_DELETED:{path}")
        if previous and TEST_PATH.search(previous) and not TEST_PATH.search(path):
            escalate.append(f"TEST_FILE_DELETED:{previous}")  # moved out of the tests: no longer a test
        if (added + deleted) > 0 and not isinstance(patch, str):
            escalate.append(f"DIFF_UNAVAILABLE:{path}")
            continue
        for line in (patch or "").splitlines():
            if line.startswith("+") and not line.startswith("+++") and any(p.search(line) for p in SECRET_PATTERNS):
                escalate.append(f"SECRET_PATTERN_IN_DIFF:{path}")
                break
    if added_total > MAX_ADDED_LINES:
        escalate.append(f"DIFF_TOO_LARGE:{added_total}")
    return refuse, sorted(set(escalate))


def diff_digest(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(files, key=lambda r: str(r.get("path"))):
        digest.update(json.dumps([row.get("path"), row.get("previous_filename"), row.get("status"),
                                  row.get("patch")], sort_keys=True).encode())
    return digest.hexdigest()


def classify_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Normalize check runs and status contexts: which are pending, which failed, whether preflight succeeded."""
    pending: list[str] = []
    failed: list[str] = []
    preflight = False
    for item in checks:
        name = str(item.get("name") or item.get("context") or item.get("workflowName") or "unnamed")
        if "state" in item and "status" not in item:  # a commit status context
            state = str(item.get("state") or "").upper()
            done, conclusion = state not in {"PENDING", "EXPECTED", ""}, state
        else:
            done = str(item.get("status") or "").upper() == "COMPLETED"
            conclusion = str(item.get("conclusion") or "").upper()
        if not done:
            pending.append(name)
        elif conclusion not in PASS_CONCLUSIONS:
            failed.append(f"{name}={conclusion or 'UNKNOWN'}")
        if done and conclusion == "SUCCESS" and PREFLIGHT_CHECK.search(name):
            preflight = True
    return {"pending": pending, "failed": failed, "preflight_success": preflight, "count": len(checks)}


# ---- the policy ------------------------------------------------------------------------------------------------------


def authorize(facts: dict[str, Any]) -> tuple[Verdict | None, dict[str, Any], list[str], list[str]]:
    """The authorization half of the policy: is this change inside what the graph on main allows?

    Returns (an early verdict when the pull request is not for this policy, the authorization record, refusals,
    escalations). It looks only at paths, the graph and the diff, never at CI state, review threads or time, which
    GitHub itself enforces, so the same function backs the merger and the GitHub scope gate."""
    repository = str(facts["repository"])
    identity = roadmap_identity(str(facts.get("title") or ""))
    author = str(facts.get("author") or "")
    progress = identity is None and is_progress_title(str(facts.get("title") or ""))
    if identity is None and not progress:
        if author.lower().startswith(("dependabot", "app/dependabot")):
            return Verdict(ESCALATE, ("DEPENDENCY_UPDATE_REQUIRES_OWNER",), {}), {}, [], []
        return Verdict(IGNORE, ("NOT_ROADMAP_PR",), {}), {}, [], []
    identity = identity or "ROADMAP-PROGRESS"
    refuse: list[str] = []
    escalate: list[str] = []
    if author not in OWNER_LOGINS:
        escalate.append(f"AUTHOR_NOT_OWNER_ACCOUNT:{author or 'unknown'}")
    if facts.get("base") != "main":
        refuse.append("BASE_NOT_MAIN")
    if str(facts.get("state") or "OPEN") != "OPEN":
        return Verdict(IGNORE, (f"PULL_REQUEST_{facts.get('state')}",), {}), {}, [], []

    # authorization: the graph on main decides what this change may touch
    authorization: dict[str, Any] = {"identity": identity, "policy": POLICY_VERSION, "graph_main_sha": facts.get("main_sha")}
    base_graph: dict[str, Any] | None = None
    try:
        base_graph = json.loads(facts["graph_base"]) if isinstance(facts.get("graph_base"), str) else None
        if base_graph is not None:
            parse_capability_graph(base_graph)
    except (ValueError, CapabilityGraphInvalidError):
        base_graph = None
        escalate.append("AUTHORITY_GRAPH_INVALID")
    files = facts.get("files") or []
    paths = sorted({str(row.get("path")) for row in files}
                   | {str(row["previous_filename"]) for row in files if row.get("previous_filename")})
    if base_graph is None:
        if "AUTHORITY_GRAPH_INVALID" not in escalate:
            escalate.append("AUTHORITY_GRAPH_UNAVAILABLE")
    else:
        grant = ({"kind": "progress", "slice": "roadmap-progress", "allowed_paths": [GRAPH_PATH], "forbidden_paths": []}
                 if progress else resolve_authorization(repository, identity, base_graph))
        if grant is None:
            escalate.append("SLICE_NOT_IN_AUTHORIZED_GRAPH")
        else:
            authorization.update(grant)
            allowed, forbidden = grant["allowed_paths"], grant["forbidden_paths"]
            outside = [p for p in paths if p != GRAPH_PATH and (not _any_match(p, allowed) or _any_match(p, forbidden))]
            if grant["kind"] in {"planning", "progress"}:
                outside = [p for p in paths if p != GRAPH_PATH]
                if GRAPH_PATH not in paths:
                    refuse.append("PLANNING_WITHOUT_GRAPH_CHANGE")
            granted = {p for e in facts.get("owner_exceptions") or [] for p in e.get("paths", [])}
            if granted & set(outside):  # an explicit, head-pinned owner approval for these exact paths only
                authorization["owner_exception"] = [e for e in facts["owner_exceptions"]]
                outside = [p for p in outside if p not in granted]
            if outside:  # widening or reinterpreting an authorization is the owner's decision, never the merger's
                escalate.append("OUT_OF_SCOPE_PATHS:" + ",".join(sorted(outside)[:8]))
            if GRAPH_PATH in paths:
                try:
                    head_graph = json.loads(facts["graph_head"]) if isinstance(facts.get("graph_head"), str) else None
                    if head_graph is None:
                        raise ValueError("graph at head unavailable")
                    parse_capability_graph(head_graph)
                    refuse.extend(graph_change_problems(base_graph, head_graph))
                    if grant["kind"] == "progress":
                        refuse.extend(progress_problems(base_graph, head_graph, facts.get("progress_evidence") or {}))
                        authorization["evidence"] = dict(facts.get("progress_evidence") or {})
                except (ValueError, CapabilityGraphInvalidError):
                    refuse.append("GRAPH_AT_HEAD_INVALID")

    # the diff itself
    diff_refuse, diff_escalate = diff_problems(files)
    refuse.extend(diff_refuse)
    escalate.extend(diff_escalate)

    return None, authorization, refuse, escalate


def scope_verdict(facts: dict[str, Any]) -> Verdict:
    """AUTHORIZED / REFUSE / ESCALATE / IGNORE for the scope gate: authorization only."""
    early, authorization, refuse, escalate = authorize(facts)
    if early is not None:
        if early.reasons == ("DEPENDENCY_UPDATE_REQUIRES_OWNER",):
            # a dependency bot's pull request is not roadmap development: the gate has nothing to say about it, and a
            # required check must never block the owner from merging it (the merger's own policy still escalates it)
            return Verdict(IGNORE, ("NOT_ROADMAP_PR",), {})
        return early
    if escalate:
        return Verdict(ESCALATE, tuple(sorted(set(escalate + refuse))), authorization)
    if refuse:
        return Verdict(REFUSE, tuple(sorted(set(refuse))), authorization)
    return Verdict(AUTHORIZED, ("WITHIN_AUTHORIZED_SCOPE",), authorization)


def evaluate(facts: dict[str, Any], *, now: datetime) -> Verdict:
    """MERGE / PROMOTE / WAIT / REFUSE / ESCALATE / IGNORE for one pull request at one instant, from gathered facts."""
    early, authorization, refuse, escalate = authorize(facts)
    if early is not None:
        return early
    wait: list[str] = []
    # verification gates
    draft = facts.get("draft") is True
    checks = classify_checks(facts.get("checks") or [])
    authorization["checks"] = {k: checks[k] for k in ("count", "preflight_success")}
    if checks["count"] == 0:
        wait.append("CHECKS_MISSING")
    if checks["failed"]:
        refuse.append("CHECK_FAILED:" + ",".join(checks["failed"][:6]))
    if checks["pending"]:
        wait.append("CHECKS_PENDING")
    if not checks["preflight_success"] and not checks["pending"] and not checks["failed"] and checks["count"]:
        refuse.append("PREFLIGHT_EVIDENCE_MISSING")
    merge_state = str(facts.get("merge_state") or "UNKNOWN")
    if merge_state == "DIRTY":
        refuse.append("MERGE_CONFLICT")
    elif not draft and merge_state != "CLEAN":
        if merge_state in {"BEHIND", "BLOCKED", "UNKNOWN", "UNSTABLE", "HAS_HOOKS"}:
            wait.append(f"MERGE_STATE_{merge_state}")
        else:
            refuse.append(f"MERGE_STATE_{merge_state}")
    if facts.get("review_decision") in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        refuse.append(f"REVIEW_{facts['review_decision']}")
    unresolved = [t for t in facts.get("threads") or [] if not t.get("resolved")]
    if unresolved:
        refuse.append(f"UNRESOLVED_REVIEW_THREADS:{len(unresolved)}")
    if not draft:
        quiet = _ts(facts.get("quiet_since"))
        if quiet is None:
            wait.append("REVIEW_ACTIVITY_UNKNOWN")
        elif (now - quiet).total_seconds() < REVIEW_SETTLE_SECONDS:
            wait.append("REVIEW_SETTLE_WINDOW")

    if escalate:
        return Verdict(ESCALATE, tuple(sorted(set(escalate + refuse))), authorization)
    if refuse:
        return Verdict(REFUSE, tuple(sorted(set(refuse))), authorization)
    if wait:
        return Verdict(WAIT, tuple(sorted(set(wait))), authorization)
    return Verdict(PROMOTE if draft else MERGE, ("ALL_GATES_PASSED",), authorization)


# ---- GitHub port and fact gathering ----------------------------------------------------------------------------------


class Port(Protocol):
    def list_open(self, repo: str) -> list[dict[str, Any]]: ...
    def files(self, repo: str, number: int) -> list[dict[str, Any]]: ...
    def file_text(self, repo: str, path: str, ref: str) -> str | None: ...
    def main_sha(self, repo: str) -> str: ...
    def threads(self, repo: str, number: int) -> list[dict[str, Any]]: ...
    def quiet_since(self, repo: str, number: int, head: str, created_at: str) -> str | None: ...
    def mark_ready(self, repo: str, number: int) -> None: ...
    def merge(self, repo: str, number: int, head: str) -> None: ...
    def merged_info(self, repo: str, number: int) -> dict[str, Any]: ...
    def pull_state(self, repo: str, number: int) -> str: ...
    def pull_summary(self, repo: str, number: int) -> dict[str, Any]: ...


class GhPort:
    """The only outward-facing operations: read pull requests, mark one ready, squash-merge one verified head."""

    FIELDS = ("number,title,isDraft,headRefOid,baseRefName,author,createdAt,mergeStateStatus,reviewDecision,"
              "statusCheckRollup,state")

    def _gh(self, args: list[str], *, allow_missing: bool = False) -> str | None:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              check=False, timeout=180)
        if done.returncode != 0:
            if allow_missing and "Not Found" in (done.stderr + done.stdout):
                return None
            raise RuntimeError("GH_FAILED: " + (done.stderr or done.stdout).strip()[-300:])
        return done.stdout

    def _paged(self, endpoint: str) -> list[dict[str, Any]]:
        raw = self._gh(["api", "--paginate", "--slurp", endpoint]) or "[]"
        pages = json.loads(raw)
        return [item for page in pages if isinstance(page, list) for item in page if isinstance(item, dict)]

    def list_open(self, repo: str) -> list[dict[str, Any]]:
        raw = self._gh(["pr", "list", "--repo", repo, "--state", "open", "--limit", str(MAX_PULLS_PER_REPOSITORY),
                        "--json", self.FIELDS]) or "[]"
        return [row for row in json.loads(raw) if isinstance(row, dict)]

    def files(self, repo: str, number: int) -> list[dict[str, Any]]:
        rows = self._paged(f"repos/{repo}/pulls/{number}/files?per_page=100")
        return [{"path": r.get("filename"), "previous_filename": r.get("previous_filename"), "status": r.get("status"),
                 "additions": r.get("additions"), "deletions": r.get("deletions"), "patch": r.get("patch")} for r in rows]

    def file_text(self, repo: str, path: str, ref: str) -> str | None:
        return self._gh(["api", "-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/{path}?ref={ref}"],
                        allow_missing=True)

    def main_sha(self, repo: str) -> str:
        return (self._gh(["api", f"repos/{repo}/commits/main", "--jq", ".sha"]) or "").strip()

    def threads(self, repo: str, number: int) -> list[dict[str, Any]]:
        owner, name = repo.split("/", 1)
        query = ("query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest("
                 "number:$number){reviewThreads(first:100){pageInfo{hasNextPage} nodes{id isResolved}}}}}")
        payload = json.loads(self._gh(["api", "graphql", "-f", f"query={query}", "-F", f"owner={owner}",
                                       "-F", f"name={name}", "-F", f"number={number}"]) or "{}")
        block = ((((payload.get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get("reviewThreads"))
        nodes = (block or {}).get("nodes")
        page = (block or {}).get("pageInfo") or {}
        if not isinstance(nodes, list) or type(page.get("hasNextPage")) is not bool or page["hasNextPage"] or len(nodes) >= 100:
            raise RuntimeError("REVIEW_THREADS_INCOMPLETE: cannot prove every conversation is resolved")
        return [{"id": str(n.get("id")), "resolved": n.get("isResolved") is True} for n in nodes if isinstance(n, dict)]

    def quiet_since(self, repo: str, number: int, head: str, created_at: str) -> str | None:
        """The latest of: PR creation, the head commit, review activity on the head, and the last ready-for-review."""
        stamps: list[datetime] = []
        created = _ts(created_at)
        if created:
            stamps.append(created)
        commit = json.loads(self._gh(["api", f"repos/{repo}/commits/{head}"]) or "{}")
        committed = _ts(((commit.get("commit") or {}).get("committer") or {}).get("date"))
        if committed is None:
            return None
        stamps.append(committed)
        for comment in self._paged(f"repos/{repo}/pulls/{number}/comments?per_page=100"):
            if str(comment.get("commit_id") or "") == head and (stamp := _ts(comment.get("updated_at") or comment.get("created_at"))):
                stamps.append(stamp)
        for review in self._paged(f"repos/{repo}/pulls/{number}/reviews?per_page=100"):
            if str(review.get("commit_id") or head) == head and (stamp := _ts(review.get("submitted_at"))):
                stamps.append(stamp)
        for event in self._paged(f"repos/{repo}/issues/{number}/events?per_page=100"):
            if event.get("event") == "ready_for_review" and (stamp := _ts(event.get("created_at"))):
                stamps.append(stamp)
        return max(stamps).isoformat()

    def mark_ready(self, repo: str, number: int) -> None:
        self._gh(["pr", "ready", str(number), "--repo", repo])

    def merge(self, repo: str, number: int, head: str) -> None:
        self._gh(["pr", "merge", str(number), "--repo", repo, "--squash", "--match-head-commit", head])

    # The scope gate runs with a read-only token that cannot read checks or commit statuses, and GitHub fails the whole
    # request when one requested field is not permitted. It judges authorization only, so it asks for no verification data.
    SCOPE_FIELDS = "number,title,isDraft,headRefOid,baseRefName,author,createdAt,state"

    def pull_summary(self, repo: str, number: int) -> dict[str, Any]:
        return dict(json.loads(self._gh(["pr", "view", str(number), "--repo", repo, "--json", self.SCOPE_FIELDS]) or "{}"))

    def pull_state(self, repo: str, number: int) -> str:
        info = json.loads(self._gh(["pr", "view", str(number), "--repo", repo, "--json", "state"]) or "{}")
        return str(info.get("state") or "UNKNOWN")

    def merged_info(self, repo: str, number: int) -> dict[str, Any]:
        return dict(json.loads(self._gh(["pr", "view", str(number), "--repo", repo, "--json", "state,mergeCommit"]) or "{}"))


def gather(port: Port, repo: str, summary: dict[str, Any], *, scope_only: bool = False) -> dict[str, Any]:
    """Every fact the policy needs. Heavy reads happen only for pull requests that claim a roadmap identity.

    ``scope_only`` skips review threads and the quiet-window reads: the GitHub scope gate judges authorization alone."""
    number = int(summary["number"])
    facts: dict[str, Any] = {
        "repository": repo, "number": number, "title": summary.get("title"),
        "author": (summary.get("author") or {}).get("login"), "draft": summary.get("isDraft") is True,
        "state": summary.get("state") or "OPEN", "base": summary.get("baseRefName"),
        "head": str(summary.get("headRefOid") or ""), "merge_state": summary.get("mergeStateStatus"),
        "review_decision": summary.get("reviewDecision") or None, "created_at": summary.get("createdAt"),
        "checks": summary.get("statusCheckRollup") or [],
    }
    progress = is_progress_title(str(facts["title"] or ""))
    if roadmap_identity(str(facts["title"] or "")) is None and not progress:
        return facts
    head = facts["head"]
    facts["main_sha"] = port.main_sha(repo)
    facts["files"] = port.files(repo, number)
    paths = {str(f.get("path")) for f in facts["files"]}
    facts["graph_base"] = port.file_text(repo, GRAPH_PATH, "main")
    facts["graph_head"] = port.file_text(repo, GRAPH_PATH, head) if GRAPH_PATH in paths else None
    if progress and facts["graph_base"] and facts["graph_head"]:
        try:
            wanted = new_evidence_urls(json.loads(facts["graph_base"]), json.loads(facts["graph_head"]))
        except ValueError:
            wanted = {}
        facts["progress_evidence"] = {}
        for url in sorted({u for urls in wanted.values() for u in urls}):
            match = PULL_URL.fullmatch(url)
            if match and match.group(1) in PORTFOLIO_REPOS:
                facts["progress_evidence"][url] = port.pull_state(match.group(1), int(match.group(2)))
            else:
                facts["progress_evidence"][url] = "NOT_AN_AUTHORIZED_REPOSITORY"
    if not scope_only:
        facts["threads"] = port.threads(repo, number)
        facts["quiet_since"] = port.quiet_since(repo, number, head, str(facts["created_at"] or ""))
    return facts


# ---- durable evidence ------------------------------------------------------------------------------------------------


class Ledger:
    """Append-only decision record. Refusals and escalations are written once per (head, reasons); merges always."""

    def __init__(self, root: Path) -> None:
        self.dir = Path(root) / "portfolio-merger"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = self.dir / "decisions.jsonl"
        self.last = self.dir / "last-decision.json"

    def _seen(self) -> dict[str, str]:
        try:
            value = json.loads(self.last.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def record(self, entry: dict[str, Any]) -> bool:
        action = str(entry["action"])
        key = hashlib.sha256(json.dumps([entry["head"], action, entry.get("reasons")], sort_keys=True).encode()).hexdigest()
        slot = f"{entry['repository']}#{entry['pr']}"
        seen = self._seen()
        if action not in ALWAYS_RECORDED and seen.get(slot) == key:
            return False
        line = json.dumps({"policy": POLICY_VERSION, "at": datetime.now(UTC).isoformat(), **entry}, sort_keys=True)
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        seen[slot] = key
        atomic_json(self.last, seen)
        return True


def _entry(facts: dict[str, Any], verdict: Verdict, action: str, **extra: Any) -> dict[str, Any]:
    files = facts.get("files") or []
    return {"repository": facts["repository"], "pr": facts["number"], "head": facts.get("head"), "action": action,
            "reasons": list(verdict.reasons), "title": facts.get("title"), "authorization": verdict.authorization,
            "files": [r.get("path") for r in files][:MAX_FILES], "diff_sha256": diff_digest(files) if files else None,
            "needs_owner": action == ESCALATE, **extra}


# ---- the cycle ---------------------------------------------------------------------------------------------------------

Advisor = Callable[[dict[str, Any], Verdict], dict[str, Any]]


def _advise(advisor: Advisor | None, facts: dict[str, Any], verdict: Verdict) -> tuple[Verdict, dict[str, Any] | None]:
    """A local model may only turn a merge into an escalation; it can never create or widen an authorization."""
    if advisor is None or verdict.action not in {MERGE, PROMOTE}:
        return verdict, None
    try:
        opinion = advisor(facts, verdict)
    except Exception as exc:  # an unavailable or failing advisor must not authorize anything either way
        return verdict, {"advisor_error": type(exc).__name__}
    if isinstance(opinion, dict) and opinion.get("concern") is True:
        return Verdict(ESCALATE, ("ADVISOR_CONCERN",), verdict.authorization), opinion
    return verdict, opinion if isinstance(opinion, dict) else None


Guard = Callable[[], "str | None"]  # None while the engine may still act; otherwise the reason it may not


EXCEPTIONS_FILE = "owner-approved-exceptions.json"


def load_exceptions(state_root: Path) -> list[dict[str, Any]]:
    """Owner-approved scope exceptions: ``[{repository, pr, head, paths, approved_by, approved_at, reason}]``.

    Each exception covers exactly one pull request at one exact head and an explicit list of paths. It never widens the
    global policy, never covers protected paths, dependencies or secrets (those escalate separately), and silently stops
    applying when the head changes. A malformed file grants nothing.
    """
    try:
        value = json.loads((Path(state_root) / "portfolio-merger" / EXCEPTIONS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [e for e in value if _valid_exception(e)]


def _valid_exception(entry: Any) -> bool:
    """Every field has the right type and shape; anything else grants nothing (and can never abort a cycle)."""
    if not isinstance(entry, dict):
        return False
    approver, head, paths = entry.get("approved_by"), entry.get("head"), entry.get("paths")
    return (isinstance(entry.get("repository"), str) and isinstance(entry.get("pr"), int) and not isinstance(entry["pr"], bool)
            and isinstance(head, str) and re.fullmatch(r"[0-9a-f]{40}", head) is not None
            and isinstance(paths, list) and bool(paths) and all(isinstance(x, str) and x for x in paths)
            and isinstance(approver, str) and approver in OWNER_LOGINS
            and isinstance(entry.get("approved_at"), str) and isinstance(entry.get("reason"), str))


def _apply_exceptions(facts: dict[str, Any], exceptions: list[dict[str, Any]]) -> dict[str, Any]:
    facts["owner_exceptions"] = [e for e in exceptions if e["repository"] == facts.get("repository")
                                 and int(e["pr"]) == int(facts["number"]) and e["head"] == facts["head"]]
    return facts


def process(port: Port, ledger: Ledger | None, repo: str, summary: dict[str, Any], *, clock: Callable[[], datetime],
            advisor: Advisor | None, dry_run: bool, guard: Guard | None = None,
            exceptions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    exceptions = exceptions or []
    facts = _apply_exceptions(gather(port, repo, summary), exceptions)
    verdict, opinion = _advise(advisor, facts, evaluate(facts, now=clock()))
    row: dict[str, Any] = {"repository": repo, "pr": facts["number"], "head": facts["head"], "decision": verdict.action,
                           "reasons": list(verdict.reasons), "merged": False, "promoted": False}
    extra = {"advisor": opinion} if opinion else {}
    if verdict.action == IGNORE:
        return row
    if dry_run or verdict.action not in {MERGE, PROMOTE}:
        if ledger is not None and not dry_run and verdict.action in {REFUSE, ESCALATE}:
            ledger.record(_entry(facts, verdict, verdict.action, **extra))
        return row
    if guard is not None and (vetoed := guard()):
        row["decision"] = vetoed
        return row
    if verdict.action == PROMOTE:
        try:
            port.mark_ready(repo, facts["number"])
        except RuntimeError as exc:
            row["decision"] = "PROMOTE_FAILED"
            if ledger is not None:
                ledger.record(_entry(facts, verdict, "PROMOTE_FAILED", error=str(exc)[:300], **extra))
            return row
        row["promoted"] = True
        if ledger is not None:
            ledger.record(_entry(facts, verdict, "PROMOTED", **extra))
        return row  # the review window restarts at promotion; the next cycle decides the merge
    # MERGE: re-gather and re-decide on a fresh read immediately before acting, then merge exactly that head
    fresh_summary = next((s for s in port.list_open(repo) if int(s.get("number", -1)) == facts["number"]), None)
    again = _apply_exceptions(gather(port, repo, fresh_summary), exceptions) if fresh_summary else None
    if again is None or again["head"] != facts["head"]:
        row["decision"] = "HEAD_CHANGED_BEFORE_MERGE"
        return row
    second, _ = _advise(advisor, again, evaluate(again, now=clock()))
    if guard is not None and (vetoed := guard()):  # owner control may have changed during the re-read
        row["decision"] = vetoed
        return row
    if second.action != MERGE:
        row.update(decision=second.action, reasons=list(second.reasons))
        if ledger is not None and second.action in {REFUSE, ESCALATE}:
            ledger.record(_entry(again, second, second.action))
        return row
    try:
        port.merge(repo, facts["number"], facts["head"])
        info = port.merged_info(repo, facts["number"])
    except RuntimeError as exc:
        row["decision"] = "MERGE_FAILED"
        if ledger is not None:
            ledger.record(_entry(again, second, "MERGE_FAILED", error=str(exc)[:300], **extra))
        return row
    merged = str(info.get("state")) == "MERGED"
    row["merged"] = merged
    if not merged:
        row["decision"] = "MERGE_NOT_CONFIRMED"
    if ledger is not None:
        ledger.record(_entry(again, second, "MERGED" if merged else "MERGE_NOT_CONFIRMED",
                             merge_commit=(info.get("mergeCommit") or {}).get("oid"), **extra))
    return row


def cycle(state_root: Path, *, repositories: tuple[str, ...] = PORTFOLIO_REPOS, port: Port | None = None,
          advisor: Advisor | None = None, dry_run: bool = False,
          clock: Callable[[], datetime] = lambda: datetime.now(UTC), guard: Guard | None = None) -> list[dict[str, Any]]:
    """One pass over the authorized repositories. Rows are compatible with green_pr_watch_loop's receipts."""
    port = port or GhPort()
    ledger = None if dry_run else Ledger(state_root)
    rows: list[dict[str, Any]] = []
    exceptions = load_exceptions(state_root)
    for repo in repositories:
        try:
            summaries = port.list_open(repo)
        except Exception as exc:
            rows.append({"repository": repo, "decision": "REPOSITORY_QUERY_FAILED", "merged": False,
                         "error_class": type(exc).__name__})
            continue
        for summary in summaries[:MAX_PULLS_PER_REPOSITORY]:
            if guard is not None and (vetoed := guard()):
                rows.append({"repository": repo, "decision": vetoed, "merged": False})
                return rows
            try:
                rows.append(process(port, ledger, repo, summary, clock=clock, advisor=advisor, dry_run=dry_run,
                                    guard=guard, exceptions=exceptions))
            except Exception as exc:  # one broken pull request never stops the others, and is itself recorded
                rows.append({"repository": repo, "pr": summary.get("number"), "head": summary.get("headRefOid"),
                             "decision": "PR_PROCESSING_FAILED", "merged": False, "error_class": type(exc).__name__})
    if ledger is not None:
        atomic_json(ledger.dir / "status.json", {"at": datetime.now(UTC).isoformat(), "policy": POLICY_VERSION,
                                                  "rows": rows})
    return rows

