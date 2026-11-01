"""Iron God portfolio rotation: one self-planned product turn at a time, round-robin.

Owner decision 2026-10-26: open Iron God to every portfolio product
repository; choose the first at random, then rotate so no repository goes
stale; start every turn from GitHub's current ``main``; publish accepted work
through a pull request that must pass the repository's own CI before it is
squash-merged; keep supervisors and workers in contact through the shared
working memory. project-orchestrator itself is excluded (its local lineage is
far ahead of GitHub and is integrated separately).

Each pass advances one fail-closed step, under the same owner controller,
single-worker model lease and calibration gate as the commissioning lane:

  dispatch -> worker (self-planned slice, change gates, canonical preflight)
  -> supervisor re-check of the exact head -> ACCEPTED_LOCAL (local ref)
  -> push branch + pull request -> every CI check green
  -> merge-readiness gate (exact verified head, settled review, no unresolved
     finding, green_pr_merger's mergeable/checks rules) -> squash merge
  -> VERIFIED Iron Memory lesson + working-memory note.

A review finding sends the pull request through a bounded repair: a directed
worker edit of the slice's module on the PR head, the same supervisor
re-check, a fast-forward push to the same pull request, fresh CI and review,
then resolution of the repaired conversations and the gate again.

A failed, cancelled, timed-out or unreported CI run leaves the pull request
open and the turn BLOCKED; nothing is ever force-pushed or merged around CI.
While the owner holds control nothing is dispatched, pushed or merged.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from . import iron_god_backlog as backlog
from . import iron_god_capable_builder as capable_builder
from . import iron_god_local_drafter as local_drafter
from . import iron_god_roadmap_progress as roadmap_progress
from . import iron_god_development_memory as development_memory
from . import iron_god_merge_readiness as readiness
from . import iron_god_owner_control as owner_controller
from . import iron_god_roadmap_refill as roadmap_refill
from . import iron_god_working_memory as working_memory
from .execution_authority import acknowledge_and_evaluate, supervisor_identity
from .iron_god_calibration import capability_gate, load_profile, remove_tree
from .iron_god_canon_lane import (
    WORKER_LEASE_ID,
    WORKER_LEASE_RUNTIME,
    default_lease_pool,
    default_resources,
    stop_worker,
    worker_alive,
)
from .learning import add_lesson, validate_memory
from .runtime_leases import RuntimeLeasePool
from .shared_memory import load_shared_memory, sync_shared_memory
from .state_io import atomic_json
from .worker_transport import (
    accept_locally,
    dispatch_canonical_slice,
    empty_ledger,
    load_ledger,
    record_independent_verification,
    record_worker_result,
    transition_packet,
    write_ledger,
)

POLICY_KEY = "portfolio_rotation"
ACTIVE_STATES = {"DISPATCHED", "IMPLEMENTING"}
PUBLICATION_OPEN = {"PUBLISH_PENDING", "AWAITING_CI"}
REPAIR_STATES = {"REPAIR_PENDING", "REPAIRING"}  # a pull request waiting for, or under, a review repair
MAX_REVIEW_REPAIRS = 2  # bounded: a finding the local model cannot fix in two directed attempts goes to the owner
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".cpp", ".h", ".hpp", ".cs")
ENVELOPE_EXCLUDED_ROOTS = {"docs", "engineering", ".github", "data", "assets", "build", "dist", "node_modules"}
ALWAYS_FORBIDDEN = ["AGENTS.md", "DoWork.txt", "CLAUDE.md", "LICENSE", "SECURITY.md", "engineering/", ".github/",
                    "docs/"]
CI_APPEAR_SECONDS = 20 * 60
CI_TOTAL_SECONDS = 3 * 60 * 60
TURN_BUDGET_MINUTES = 120
CANON_HANDBACK_FIELDS = {"schema_version", "packet_id", "project", "repository", "branch", "head_sha", "probe_id",
                         "pattern_id", "evidence_ref", "observed_at", "status", "summary", "changed_paths",
                         "verification"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _git(cwd: Path, *args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False,
                          timeout=timeout, encoding="utf-8", errors="replace")


def rotation_config(policy: dict[str, Any] | None) -> dict[str, Any] | None:
    config = (policy or {}).get(POLICY_KEY)
    return config if isinstance(config, dict) and config.get("enabled") is True else None


def product_repositories(release_repo: Path, config: dict[str, Any]) -> list[str]:
    portfolio = json.loads((Path(release_repo) / "engineering" / "portfolio.json").read_text(encoding="utf-8"))
    excluded = set(config.get("exclude", []))
    return sorted({repo["name"] for project in portfolio.get("projects", [])
                   for repo in project.get("repositories", [])
                   if isinstance(repo, dict) and repo.get("name") and repo["name"] not in excluded})


def memory_project(release_repo: Path, repo: str) -> str:
    portfolio = json.loads((Path(release_repo) / "engineering" / "portfolio.json").read_text(encoding="utf-8"))
    for project in portfolio.get("projects", []):
        if any(isinstance(r, dict) and r.get("name") == repo for r in project.get("repositories", [])):
            return str(project.get("id") or "portfolio")
    return "portfolio"


class ProductLane:
    """Filesystem layout of the product rotation, beside the commissioning lane."""

    def __init__(self, state_root: Path):
        self.state_root = Path(state_root)
        self.root = self.state_root / "iron-god-products"
        self.ledger_path = self.root / "ledger.json"
        self.active_path = self.root / "active.json"
        self.rotation_path = self.root / "rotation.json"
        self.publications_path = self.root / "publications.json"
        self.packets = self.root / "packets"
        self.handbacks = self.root / "handbacks"
        self.reports = self.root / "reports"
        self.workspaces = self.state_root.parent / "canon-workspaces" / "products"
        self.worktrees = self.workspaces / "worktrees"

    def clone(self, repo: str) -> Path:
        return self.workspaces / repo.split("/", 1)[-1]

    def ledger(self) -> dict[str, Any]:
        return load_ledger(self.ledger_path) if self.ledger_path.exists() else empty_ledger()

    def save(self, ledger: dict[str, Any], before: dict[str, Any]) -> None:
        write_ledger(self.ledger_path, ledger,
                     expected_revision=before["revision"] if self.ledger_path.exists() else None)

    def _json(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def active(self) -> dict[str, Any] | None:
        return self._json(self.active_path, None)

    def rotation(self) -> dict[str, Any]:
        return self._json(self.rotation_path, {"schema_version": 1, "order": [], "cursor": -1, "turns": {}})

    def publications(self) -> dict[str, Any]:
        return self._json(self.publications_path, {"schema_version": 1, "items": {}})


def _report(lane: ProductLane, payload: dict[str, Any]) -> dict[str, Any]:
    payload = {"schema_version": 1, "observed_at_utc": _now(), **payload}
    lane.root.mkdir(parents=True, exist_ok=True)
    atomic_json(lane.root / "lane-status.json", payload)
    return payload


def next_repository(lane: ProductLane, repos: list[str], busy: set[str],
                    rng: random.Random | None = None) -> str | None:
    """Round-robin over ``repos``: a random first pick, then strictly in rotation, skipping busy repositories.

    The order is shuffled once and persisted; repositories added later join at a
    random position, removed ones drop out, and the cursor survives restarts.
    """
    rotation = lane.rotation()
    order = [r for r in rotation.get("order", []) if r in repos]
    newcomers = [r for r in repos if r not in order]
    chooser = rng or random.SystemRandom()
    for repo in newcomers:
        order.insert(chooser.randint(0, len(order)), repo)
    if not order:
        return None
    if rotation.get("order") != order:
        last = rotation.get("order", [])[rotation.get("cursor", -1)] if 0 <= rotation.get("cursor", -1) < len(
            rotation.get("order", [])) else None
        rotation["cursor"] = order.index(last) if last in order else -1
        rotation["order"] = order
        rotation.setdefault("seeded_at_utc", _now())
        atomic_json(lane.rotation_path, rotation)
    start = rotation.get("cursor", -1)
    if start < 0:  # first ever pick: random, then rotation continues from it
        start = chooser.randrange(len(order)) - 1
    for offset in range(1, len(order) + 1):
        repo = order[(start + offset) % len(order)]
        if repo not in busy:
            return repo
    return None


def _advance_cursor(lane: ProductLane, repo: str, packet_id: str) -> None:
    rotation = lane.rotation()
    rotation["cursor"] = rotation["order"].index(repo)
    rotation.setdefault("turns", {})[repo] = {"last_turn_at_utc": _now(), "packet_id": packet_id}
    atomic_json(lane.rotation_path, rotation)


def sync_clone(lane: ProductLane, repo: str) -> tuple[Path, str]:
    """The lane's own clone of ``repo``, fetched from GitHub; returns it and the current origin/main SHA."""
    clone = lane.clone(repo)
    if not (clone / ".git").exists():
        lane.workspaces.mkdir(parents=True, exist_ok=True)
        cloned = subprocess.run(["gh", "repo", "clone", repo, str(clone), "--", "--filter=blob:none"],
                                capture_output=True, text=True, check=False, timeout=1800)
        if cloned.returncode != 0:
            raise RuntimeError("PRODUCT_CLONE_FAILED: " + cloned.stderr[-300:])
    origin = _git(clone, "remote", "get-url", "origin")
    if origin.returncode != 0 or repo.lower() not in origin.stdout.lower():
        raise RuntimeError("PRODUCT_CLONE_ORIGIN_MISMATCH")
    fetched = _git(clone, "fetch", "--prune", "origin", "main", timeout=1800)
    if fetched.returncode != 0:
        raise RuntimeError("PRODUCT_FETCH_FAILED: " + fetched.stderr[-300:])
    base = _git(clone, "rev-parse", "origin/main").stdout.strip()
    if len(base) != 40:
        raise RuntimeError("PRODUCT_MAIN_UNRESOLVED")
    return clone, base


def scope_envelope(clone: Path, base: str) -> list[str]:
    """Top-level code and test directories the self-planned slice may change."""
    listed = _git(clone, "ls-tree", "-r", "--name-only", base)
    roots: set[str] = set()
    for path in listed.stdout.splitlines():
        top, sep, _rest = path.partition("/")
        if sep and top not in ENVELOPE_EXCLUDED_ROOTS and not top.startswith(".") and path.endswith(SOURCE_SUFFIXES):
            roots.add(top + "/")
    return sorted(roots)[:32]


def python_packages(clone: Path, base: str, roots: list[str]) -> list[str]:
    """Real packages (``__init__.py`` directories) inside the envelope, excluding tests."""
    listed = _git(clone, "ls-tree", "-r", "--name-only", base).stdout.splitlines()
    return sorted({p.rsplit("/", 1)[0] + "/" for p in listed if p.endswith("/__init__.py") and under(p, roots)
                   and not any(part in {"tests", "test"} for part in p.split("/"))})


def record_readiness(lane: ProductLane, repo: str, base: str, *, ready: bool, reason: str = "") -> None:
    readiness = lane._json(lane.root / "readiness.json", {"schema_version": 1, "repositories": {}})
    readiness.setdefault("repositories", {})[repo] = {"repository": repo, "base_sha": base, "ready": ready,
                                                       "reason": reason, "checked_at_utc": _now()}
    atomic_json(lane.root / "readiness.json", readiness)


def open_pr_paths(repo: str) -> list[str]:
    """Paths owned by any open pull request; a turn never touches files under review."""
    proc = subprocess.run(["gh", "pr", "list", "--repo", repo, "--state", "open", "--limit", "100",
                           "--json", "files"], capture_output=True, text=True, check=False, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError("OPEN_PR_PATHS_UNAVAILABLE")
    return sorted({f["path"] for pr in json.loads(proc.stdout or "[]") for f in pr.get("files", []) if f.get("path")})


def build_product_packet(repo: str, *, packet_id: str, base: str, worktree: Path, branch: str,
                         roots: list[str], avoid: list[str], memory_text: str, project: str,
                         attempted: list[str] | None = None, item: dict[str, Any] | None = None) -> dict[str, Any]:
    objective = (f"Choose and implement one small, high-value roadmap slice for {repo} within the scope envelope, "
                 "with a regression test, passing the repository's canonical preflight.")
    if item is not None:  # an approved backlog slice: built as written, never re-planned
        objective = f"[{item['id']}] {item['objective']}"
        attempted = []
    verification = ["python engineering/preflight.py"]
    packet = {
        "packet_id": packet_id, "project": project, "repository": repo,
        "source_ref": f"github:{repo}@origin/main:{base}", "base_sha": base, "branch": branch,
        "task": objective, "paths": roots, "verification": verification, "worktree": str(worktree),
        "runner_id": "aider-ollama", "avoid_paths": avoid, "working_memory": memory_text,
        "preflight_timeout_s": 7200, "attempted_functions": sorted(attempted or []),
        "planning": {
            "vision_ref": f"{repo} AGENTS.md and README.md (read-only worker context)",
            "roadmap_ref": f"{repo} roadmap as described in its own documentation",
            "architecture_ref": f"{repo} at origin/main {base}",
            "current_state_ref": "shared working memory excerpt in the packet",
            "dependency_evidence_ref": f"GitHub origin/main fetched at dispatch: {base}",
            "source_head_sha": base, "objective": objective,
            "acceptance_criteria": ["exactly one coherent slice inside the scope envelope",
                                    "a pytest regression test covers the change",
                                    "the builder change gates pass (protected paths, quality, diff size)",
                                    "the repository's canonical preflight passes at the exact head",
                                    "every CI check on the pull request passes before merge"],
            "allowed_paths": roots, "forbidden_paths": ALWAYS_FORBIDDEN, "verification_commands": verification,
            "expected_artifacts": ["a commit on the turn branch changing files only inside the scope envelope"],
            "run_budget_minutes": TURN_BUDGET_MINUTES,
            "handoff_instructions": "Implement one self-chosen slice within the envelope, run the canonical "
                                    "preflight, and hand back exact evidence plus a one-sentence note.",
            "escalation_policy": "ASK_SUPERVISOR_AND_PAUSE",
        },
    }
    if item is not None:
        packet["slice"] = {key: item.get(key) for key in ("id", "title", "roadmap_ref", "addresses", "why",
                                                          "consumer", "level", "build", "direct")}
        packet["planning"]["roadmap_ref"] = item["roadmap_ref"]
        packet["planning"]["handoff_instructions"] = ("Build the approved backlog slice exactly as specified, run "
                                                      "the canonical preflight, and hand back exact evidence.")
    return packet


def spawn_product_worker(release_repo: Path, packet_path: Path, handback_path: Path, *, release_sha: str,
                         log_path: Path) -> dict[str, Any]:
    env = {**os.environ, "PYTHONPATH": str(Path(release_repo) / "src"), "IRON_GOD_ACCEPTED_SHA": release_sha}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(release_repo) / "scripts" / "iron_god_product_worker.py"),
             "--packet", str(packet_path), "--handback", str(handback_path)],
            cwd=str(release_repo), env=env, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=flags,
        )
    return {"pid": process.pid, "process_start_fingerprint": owner_controller.process_start_fingerprint(process.pid)
            or "", "host": owner_controller.socket.gethostname()}


def default_gates(release_repo: Path) -> Callable[[Path, str, list[str]], str | None]:
    """The builder's change gates from the pinned release (the same code the worker ran)."""
    root = str(Path(release_repo))
    if root not in sys.path:
        sys.path.insert(0, root)
    from scripts.autonomous_slice_builder import generated_change_failure

    return lambda worktree, base, paths: generated_change_failure(worktree, base, paths)


def under(path: str, roots: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(normalized == r.rstrip("/") or normalized.startswith(r.rstrip("/") + "/") for r in roots)


def changed_between(clone: Path, base: str, head: str) -> list[str]:
    diff = _git(clone, "diff", "--name-only", base, head)
    return sorted({line.strip() for line in diff.stdout.splitlines() if line.strip()})


def supervisor_recheck(lane: ProductLane, packet: dict[str, Any], head: str,
                       gates: Callable[[Path, str, list[str]], str | None]) -> tuple[bool, list[dict[str, Any]]]:
    """Re-check the exact handed-back head in a fresh detached worktree (scope and change gates)."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail[:600]})

    clone = lane.clone(packet["repository"])
    base = packet["base_sha"]
    add("candidate_commit_present", _git(clone, "cat-file", "-e", head + "^{commit}").returncode == 0, head)
    add("candidate_descends_from_exact_base",
        _git(clone, "merge-base", "--is-ancestor", base, head).returncode == 0 and head != base, base)
    if not all(c["ok"] for c in checks):
        return False, checks
    changed = changed_between(clone, base, head)
    forbidden = [*packet["planning"]["forbidden_paths"], *packet.get("avoid_paths", [])]
    outside = [p for p in changed if not under(p, packet["paths"]) or under(p, forbidden)]
    add("changed_paths_within_envelope", bool(changed) and not outside, ", ".join(outside or changed)[:600])
    verify_dir = lane.workspaces / "verify" / packet["packet_id"]
    _git(clone, "worktree", "remove", "--force", str(verify_dir))
    remove_tree(verify_dir)
    verify_dir.parent.mkdir(parents=True, exist_ok=True)
    added = _git(clone, "worktree", "add", "--detach", str(verify_dir), head)
    add("fresh_verification_worktree", added.returncode == 0, added.stderr)
    if added.returncode == 0:
        try:
            failure = gates(verify_dir, base, changed)
            add("builder_change_gates", failure is None, failure or "")
        finally:
            _git(clone, "worktree", "remove", "--force", str(verify_dir))
            remove_tree(verify_dir)
    return all(c["ok"] for c in checks), checks


def record_product_memory(release_repo: Path, packet: dict[str, Any], *, pr: dict[str, Any],
                          note: str, memory_root: Path | None) -> dict[str, Any]:
    lesson = {
        "project": memory_project(release_repo, packet["repository"]),
        "repository": packet["repository"],
        "source_type": "success_pattern",
        "source_ref": f"{pr['url']}@{pr['merged_sha']}",
        "symptom": (note or packet["task"])[:400],
        "root_cause": f"Iron God product turn {packet['packet_id']} chose this slice at origin/main {packet['base_sha']}.",
        "fix": f"Merged pull request #{pr['number']} at {pr['merged_sha']}.",
        "verification": "Worker canonical preflight at the exact head, supervisor re-check of scope and change "
                        "gates, and every CI check on the pull request passed before the squash merge.",
        "regression_protection": "The slice's own regression test and the repository's CI.",
        "residual_risk": "Self-planned by the local model; value judged by the repository's own gates and CI.",
        "evidence_state": "VERIFIED",
    }
    try:
        memory = load_shared_memory(memory_root)
    except (OSError, ValueError):
        memory = validate_memory({"schema_version": 1, "entries": []})
    return sync_shared_memory(add_lesson(memory, lesson), memory_root)


class GitHub:
    """The only outward-facing operations of the lane: push, open, inspect and merge one pull request."""

    PR_FIELDS = ("number,headRefOid,baseRefName,state,isDraft,mergeStateStatus,reviewDecision,"
                 "statusCheckRollup,createdAt,author")

    def __init__(self, release_repo: Path | None = None) -> None:
        self.release_repo = release_repo
        self._merger: Any = None

    @property
    def merger(self) -> Any:
        """The portfolio's established review gate (scripts/green_pr_merger.py of the running release)."""
        if self._merger is None:
            self._merger = readiness.load_merger(self.release_repo or Path(__file__).resolve().parents[2])
        return self._merger

    def pull_request(self, repo: str, number: int) -> dict[str, Any]:
        return dict(self.merger.gh_json(["pr", "view", str(number), "--repo", repo, "--json", self.PR_FIELDS]))

    def review_threads(self, repo: str, number: int) -> list[dict[str, Any]]:
        return list(self.merger.review_threads(repo, number))

    def review_settled(self, repo: str, number: int, head: str, created_at: str) -> bool:
        return bool(self.merger.review_settled(repo, number, head, created_at))

    def age_seconds(self, created_at: str) -> float:
        return float(self.merger._age_seconds(created_at))

    def resolve_thread(self, thread_id: str) -> None:
        query = "mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{isResolved}}}"
        done = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}", "-f", f"t={thread_id}"],
                              capture_output=True, text=True, check=False, timeout=300)
        if done.returncode != 0:
            raise RuntimeError("THREAD_RESOLVE_FAILED: " + done.stderr[-300:])

    def push(self, clone: Path, branch: str, head: str) -> None:
        pushed = _git(clone, "push", "origin", f"{head}:refs/heads/{branch}", timeout=1800)
        if pushed.returncode != 0:
            raise RuntimeError("PUSH_FAILED: " + pushed.stderr[-300:])

    def open_pr(self, repo: str, branch: str, title: str, body: str) -> dict[str, Any]:
        made = subprocess.run(["gh", "pr", "create", "--repo", repo, "--base", "main", "--head", branch,
                               "--title", title, "--body", body], capture_output=True, text=True, check=False,
                              timeout=300)
        if made.returncode != 0:
            raise RuntimeError("PR_CREATE_FAILED: " + made.stderr[-300:])
        url = made.stdout.strip().splitlines()[-1]
        return {"url": url, "number": int(url.rstrip("/").rsplit("/", 1)[-1])}

    def checks(self, repo: str, number: int) -> list[dict[str, Any]]:
        proc = subprocess.run(["gh", "pr", "checks", str(number), "--repo", repo, "--json", "name,state,bucket"],
                              capture_output=True, text=True, check=False, timeout=300)
        if proc.returncode not in (0, 1, 8):  # gh exits 1/8 while checks fail/pend; output is still JSON
            raise RuntimeError("PR_CHECKS_UNAVAILABLE: " + proc.stderr[-200:])
        try:
            return json.loads(proc.stdout or "[]")
        except ValueError:
            return []  # "no checks reported" prints text, not JSON

    def merge(self, repo: str, number: int, head: str) -> str:
        merged = subprocess.run(["gh", "pr", "merge", str(number), "--repo", repo, "--squash", "--delete-branch",
                                 "--match-head-commit", head], capture_output=True, text=True, check=False,
                                timeout=600)
        if merged.returncode != 0:
            raise RuntimeError("MERGE_FAILED: " + merged.stderr[-300:])
        view = subprocess.run(["gh", "pr", "view", str(number), "--repo", repo, "--json", "mergeCommit,state"],
                              capture_output=True, text=True, check=False, timeout=300)
        data = json.loads(view.stdout or "{}")
        if data.get("state") != "MERGED":
            raise RuntimeError("MERGE_UNCONFIRMED")
        return (data.get("mergeCommit") or {}).get("oid", "")


def ci_verdict(checks: list[dict[str, Any]], *, published_at: float, now: float) -> str:
    """PASS only when checks exist and every one passed (or was skipped); otherwise WAIT or a BLOCKED reason."""
    buckets = [c.get("bucket") for c in checks]
    if any(b in {"fail", "cancel"} for b in buckets):
        return "BLOCKED_CI_FAILED"
    if not checks:
        return "BLOCKED_NO_CI_REPORTED" if now - published_at > CI_APPEAR_SECONDS else "WAIT"
    if any(b not in {"pass", "skipping"} for b in buckets):
        return "BLOCKED_CI_TIMEOUT" if now - published_at > CI_TOTAL_SECONDS else "WAIT"
    return "PASS" if "pass" in buckets else "BLOCKED_NO_CI_REPORTED"


def _record_slice_outcome(lane: ProductLane, packet_id: str, outcome: str) -> None:
    """Mirror a publication outcome onto its backlog item, if the turn built one."""
    try:
        packet = json.loads((lane.packets / f"{packet_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if packet.get("slice"):
        backlog.record_item(lane.root, packet["slice"]["id"], packet_id=packet_id, outcome=outcome)


def _save_publications(lane: ProductLane, items: dict[str, Any]) -> None:
    atomic_json(lane.publications_path, {"schema_version": 1, "items": items})


def _verified(lane: ProductLane, verifier: str, head: str) -> bool:
    """The lane's own supervisor re-check accepted exactly ``head`` (the slice's acceptance evidence)."""
    report = lane._json(lane.reports / f"{verifier}-verification.json", {})
    return report.get("passed") is True and report.get("head_sha") == head


def merge_readiness(lane: ProductLane, github: Any, packet_id: str, item: dict[str, Any]) -> readiness.Readiness:
    """Gather the live pull-request facts and ask the merge-readiness gate (see iron_god_merge_readiness)."""
    repo, number, head = item["repository"], int(item["pr"]["number"]), item["head_sha"]
    pr = github.pull_request(repo, number)
    current = str(pr.get("headRefOid") or "")
    settled = current == head and github.review_settled(repo, number, current, str(pr.get("createdAt")))
    return readiness.assess(
        pr, github.review_threads(repo, number), verified_head=head,
        verification_ok=_verified(lane, str(item.get("verified_by") or packet_id), head), settled=settled,
        age_seconds=github.age_seconds(str(pr.get("createdAt"))),
        repaired_thread_ids={str(t) for t in item.get("repaired_threads", [])}, merger=github.merger)


def _request_review_repair(lane: ProductLane, packet_id: str, item: dict[str, Any],
                           gate: readiness.Readiness) -> None:
    """Record the findings against the PR, commit, file and slice; queue a bounded repair or block."""
    repo = item["repository"]
    attempts = int(item.get("repair_attempts", 0))
    plan = lane._json(lane.handbacks / f"{packet_id}.json", {}).get("plan") or {}
    module, test = plan.get("module_path"), plan.get("test_path")
    slice_id = (lane._json(lane.packets / f"{packet_id}.json", {}).get("slice") or {}).get("id")
    record = {"packet_id": packet_id, "pr": item["pr"], "head_sha": item["head_sha"], "slice": slice_id,
              "module_path": module, "findings": gate.findings, "recorded_at_utc": _now()}
    atomic_json(lane.reports / f"{packet_id}-review-findings-{attempts + 1}.json", record)
    out_of_scope = sorted({str(f.get("path")) for f in gate.findings if f.get("path") != module})
    if attempts >= MAX_REVIEW_REPAIRS:
        reason = f"REVIEW_REPAIR_EXHAUSTED after {attempts} attempt(s)"
    elif not module or out_of_scope:
        reason = "REVIEW_FINDING_OUT_OF_REPAIR_SCOPE: " + ", ".join(out_of_scope or ["(no planned module)"])
    else:
        item.update(state="REPAIR_PENDING", findings=gate.findings, repair_module=module, repair_test=test)
        working_memory.record(lane.state_root, repo, "REVIEW_FINDINGS_RECORDED", packet_id=packet_id,
                              detail=f"{len(gate.findings)} finding(s) on {module}; repair queued",
                              pr={**item["pr"], "state": "REPAIR_PENDING"})
        return
    item.update(state="BLOCKED", reason=reason[:300], findings=gate.findings)
    _record_slice_outcome(lane, packet_id, "PR_BLOCKED")
    working_memory.record(lane.state_root, repo, "REVIEW_REPAIR_BLOCKED", packet_id=packet_id, outcome="BLOCKED",
                          blocker=f"{reason[:200]} on pull request #{item['pr']['number']}",
                          pr={**item["pr"], "state": "OPEN_BLOCKED"})


def advance_publications(lane: ProductLane, release_repo: Path, *, github: GitHub, memory_root: Path | None,
                         now: Callable[[], float] = time.time, merge: str = "auto") -> list[dict[str, Any]]:
    """Move every open publication one step: push+PR, then CI, then squash merge (authority already checked).

    With ``merge="review"`` a green pull request waits for the owner on GitHub instead of being merged.
    """
    items = lane.publications()["items"]
    events = []
    for packet_id, item in sorted(items.items()):
        if item["state"] not in PUBLICATION_OPEN:
            continue
        repo = item["repository"]
        try:
            if item["state"] == "PUBLISH_PENDING" and item.get("pr"):
                # A verified review repair: fast-forward the same pull request (never forced), then fresh CI.
                github.push(lane.clone(repo), item["branch"], item["head_sha"])
                item.update(state="AWAITING_CI", published_at=now())
                working_memory.record(lane.state_root, repo, "REVIEW_REPAIR_PUSHED", packet_id=packet_id,
                                      pr={**item["pr"], "state": "AWAITING_CI"})
            elif item["state"] == "PUBLISH_PENDING":
                github.push(lane.clone(repo), item["branch"], item["head_sha"])
                pr = github.open_pr(repo, item["branch"], item["title"], item["body"])
                item.update(state="AWAITING_CI", pr=pr, published_at=now())
                working_memory.record(lane.state_root, repo, "PULL_REQUEST_OPENED", packet_id=packet_id,
                                      pr={"number": pr["number"], "state": "AWAITING_CI", "url": pr["url"]})
            else:
                verdict = ci_verdict(github.checks(repo, item["pr"]["number"]),
                                     published_at=item["published_at"], now=now())
                if verdict == "PASS" and merge == "review":
                    item.update(state="AWAITING_OWNER_REVIEW", ci_passed_at_utc=_now())
                    working_memory.record(lane.state_root, repo, "AWAITING_OWNER_REVIEW", packet_id=packet_id,
                                          outcome="CI_GREEN_AWAITING_OWNER",
                                          pr={**item["pr"], "state": "CI_GREEN_AWAITING_OWNER"})
                elif verdict == "PASS":
                    gate = merge_readiness(lane, github, packet_id, item)
                    item["readiness"] = {"status": gate.status, "reason": gate.reason, "at_utc": _now()}
                    if gate.status == readiness.MERGE:
                        # Review findings and the PR head can change after the first gate.
                        # Refresh immediately before executing the irreversible merge.
                        fresh = merge_readiness(lane, github, packet_id, item)
                        item["readiness"] = {"status": fresh.status, "reason": fresh.reason, "at_utc": _now()}
                        if fresh.status == readiness.REPAIR:
                            _request_review_repair(lane, packet_id, item, fresh)
                        elif fresh.status != readiness.MERGE:
                            raise RuntimeError("MERGE_READINESS_CHANGED: " + fresh.reason)
                        else:
                            merged_sha = github.merge(repo, item["pr"]["number"], item["head_sha"])
                            item.update(state="MERGED", merged_sha=merged_sha, merged_at_utc=_now())
                            packet = json.loads((lane.packets / f"{packet_id}.json").read_text(encoding="utf-8"))
                            record_product_memory(release_repo, packet, note=item.get("note", ""),
                                                  memory_root=memory_root, pr={**item["pr"], "merged_sha": merged_sha})
                            working_memory.record(lane.state_root, repo, "MERGED", packet_id=packet_id,
                                                  outcome="MERGED", pr={**item["pr"], "state": "MERGED"})
                            _record_slice_outcome(lane, packet_id, "MERGED")
                    elif gate.status == readiness.REPAIR:
                        _request_review_repair(lane, packet_id, item, gate)
                    elif gate.status == readiness.RESOLVE:
                        # New review feedback can arrive after the first snapshot, before a resolution.
                        # Read the current head and comments again immediately before mutating threads.
                        # Never clear a thread whose latest review now demands another repair.
                        fresh = merge_readiness(lane, github, packet_id, item)
                        item["readiness"] = {"status": fresh.status, "reason": fresh.reason, "at_utc": _now()}
                        expected_threads = {str(row["thread_id"]) for row in gate.findings}
                        fresh_threads = {str(row["thread_id"]) for row in fresh.findings}
                        if fresh.status == readiness.REPAIR:
                            _request_review_repair(lane, packet_id, item, fresh)
                        elif fresh.status != readiness.RESOLVE or fresh_threads != expected_threads:
                            raise RuntimeError("REVIEW_RESOLUTION_STATE_CHANGED: fresh review evidence required")
                        else:
                            for row in fresh.findings:
                                github.resolve_thread(str(row["thread_id"]))
                            item.setdefault("resolved_threads", []).extend(row["thread_id"] for row in fresh.findings)
                            working_memory.record(lane.state_root, repo, "REVIEW_THREADS_RESOLVED", packet_id=packet_id,
                                                  detail=f"{len(fresh.findings)} repaired finding(s) resolved",
                                                  pr={**item["pr"], "state": "AWAITING_MERGE_STATE"})
                    elif gate.status == readiness.WAIT and now() - item["published_at"] <= CI_TOTAL_SECONDS:
                        pass  # review still settling or GitHub still computing mergeability
                    else:
                        reason = ("MERGE_READINESS_TIMEOUT: " + gate.reason if gate.status == readiness.WAIT
                                  else gate.reason)
                        item.update(state="BLOCKED", reason=reason)
                        _record_slice_outcome(lane, packet_id, "PR_BLOCKED")
                        working_memory.record(lane.state_root, repo, "MERGE_READINESS_BLOCKED", packet_id=packet_id,
                                              outcome="BLOCKED",
                                              blocker=f"{reason} on pull request #{item['pr']['number']}",
                                              pr={**item["pr"], "state": "OPEN_BLOCKED"})
                elif verdict != "WAIT":
                    item.update(state="BLOCKED", reason=verdict)
                    _record_slice_outcome(lane, packet_id, "PR_BLOCKED")
                    working_memory.record(lane.state_root, repo, verdict, packet_id=packet_id, outcome="BLOCKED",
                                          blocker=f"{verdict} on pull request #{item['pr']['number']}",
                                          pr={**item["pr"], "state": "OPEN_BLOCKED"})
        except Exception as exc:  # A GitHub failure blocks this publication only; it never crashes the lane.
            item.update(state="BLOCKED", reason=f"{type(exc).__name__}: {str(exc)[:300]}")
            _record_slice_outcome(lane, packet_id, "PR_BLOCKED")
            working_memory.record(lane.state_root, repo, "PUBLICATION_FAILED", packet_id=packet_id,
                                  outcome="BLOCKED", blocker=item["reason"])
        events.append({"packet_id": packet_id, "state": item["state"], "reason": item.get("reason")})
        _save_publications(lane, items)
    return events


def harvest(lane: ProductLane, release_repo: Path, ledger: dict[str, Any], active: dict[str, Any], *,
            gates: Callable[[Path, str, list[str]], str | None]) -> dict[str, Any]:
    packet_id = active["packet_id"]
    packet = json.loads((lane.packets / f"{packet_id}.json").read_text(encoding="utf-8"))
    repo = packet["repository"]
    try:
        handback = json.loads((lane.handbacks / f"{packet_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        handback = {"project": packet["project"], "repository": repo, "branch": packet["branch"],
                    "head_sha": packet["base_sha"], "probe_id": packet_id, "pattern_id": "iron-god-product-turn",
                    "evidence_ref": f"local://iron-god-products/{packet_id}/missing-handback", "observed_at": _now(),
                    "status": "ENVIRONMENT_FAILURE", "summary": "WORKER_EXITED_WITHOUT_HANDBACK"}
    note = str(handback.get("note") or "")
    current = ledger
    if current["packets"][packet_id]["state"] == "DISPATCHED":
        current = transition_packet(current, packet_id, state="IMPLEMENTING")
    worker_result = {k: v for k, v in handback.items() if k in CANON_HANDBACK_FIELDS}  # diagnostics stay in reports
    atomic_json(lane.reports / f"{packet_id}-handback-diagnostics.json",
                {k: v for k, v in handback.items() if k not in CANON_HANDBACK_FIELDS})
    slice_plan = handback.get("plan") or {}
    atomic_json(lane.reports / f"{packet_id}-transcript.json", {"tail": str(handback.get("transcript_tail", ""))})
    current = record_worker_result(current, packet_id, worker_result)
    outcome: dict[str, Any] = {"packet_id": packet_id, "repository": repo, "worker_status": handback["status"],
                               "worker_summary": handback.get("summary"), "head_sha": handback["head_sha"]}
    if handback["status"] == "PASS":
        head = handback["head_sha"]
        current = transition_packet(current, packet_id, state="PREFLIGHT_UNVERIFIED", exact_head_sha=head,
                                    evidence_ref=f"local://iron-god-products/{packet_id}/handback")
        passed, checks = supervisor_recheck(lane, packet, head, gates)
        atomic_json(lane.reports / f"{packet_id}-verification.json",
                    {"packet_id": packet_id, "head_sha": head, "passed": passed, "checks": checks,
                     "worker_verification": handback.get("verification", []), "verified_at_utc": _now()})
        current = record_independent_verification(current, packet_id, verified_head_sha=head, passed=passed,
                                                   checks=checks,
                                                   evidence_ref=f"local://iron-god-products/{packet_id}/verification")
        outcome.update(verification_passed=passed, failed_checks=[c["name"] for c in checks if not c["ok"]])
        if passed:
            ref = f"refs/iron-god/accepted/{packet_id.lower()}"
            _git(lane.clone(repo), "update-ref", ref, head)
            current = accept_locally(current, packet_id, accepted_ref=ref, accepted_sha=head,
                                     evidence_ref=f"local://iron-god-products/{packet_id}/verification")
        if passed and packet.get("repair"):
            # The repaired head replaces the pull request's verified head; its findings are now repaired.
            items = lane.publications()["items"]
            pub = items[packet["repair"]["publication"]]
            pub.update(state="PUBLISH_PENDING", head_sha=head, verified_by=packet_id,
                       repaired_threads=sorted({*pub.get("repaired_threads", []),
                                                *(str(f["thread_id"]) for f in packet["repair"]["findings"])}))
            pub.pop("findings", None)
            _save_publications(lane, items)
            outcome["status"] = "REPAIR_ACCEPTED"
        elif passed:
            items = lane.publications()["items"]
            item = packet.get("slice")
            roadmap = (f"Backlog item: {item['id']} ({item['title']})\nRoadmap: {item['roadmap_ref']}\n"
                       f"Addresses: {item['addresses']}\nWhy: {item['why']}\nConsumer: {item['consumer']}\n\n"
                       if item else "")
            items[packet_id] = {
                "repository": repo, "branch": packet["branch"], "head_sha": head, "state": "PUBLISH_PENDING",
                "note": note,
                "title": (f"Iron God [{item['id']}]: {item['title']}"[:120] if item else
                          f"Iron God: add {slice_plan['function_name']} to {slice_plan['module_path']}"
                          if slice_plan.get("function_name") else f"Iron God: {(note or 'autonomous slice')[:90]}"),
                "body": ("Autonomous Iron God product turn " + packet_id + ".\n\n" + roadmap +
                         f"Base: origin/main {packet['base_sha']}\nHead: {head}\n\n"
                         f"Slice: {slice_plan.get('objective', '(none)')}\n"
                         f"Files: {', '.join(handback.get('changed_paths', []))}\n"
                         + "".join(f"Example corrected by independent re-derivation: {c['call']} returns {c['now']} "
                                   f"(plan said {c['was']})\n" for c in handback.get("adjudicated_examples", []))
                         +
                         f"Worker note: {note or '(none)'}\n\n"
                         "Local Ollama/Aider produced the change inside the turn's scope envelope; the worker ran "
                         "the canonical preflight at the exact head and the supervisor re-checked scope and change "
                         "gates. Iron God merges only after every CI check on this pull request passes."),
            }
            _save_publications(lane, items)
            outcome["status"] = "ACCEPTED_LOCAL"
        else:
            outcome["status"] = "BLOCKED"
    else:
        outcome["status"] = current["packets"][packet_id]["state"]
    if packet.get("repair") and outcome["status"] != "REPAIR_ACCEPTED":
        # Bounded: another directed attempt while the budget lasts, then the pull request is blocked
        # with the findings and the failed attempts on record (never the same unbounded retry).
        items = lane.publications()["items"]
        pub = items[packet["repair"]["publication"]]
        if int(pub.get("repair_attempts", 0)) < MAX_REVIEW_REPAIRS:
            pub.update(state="REPAIR_PENDING")
        else:
            pub.update(state="BLOCKED", reason=f"REVIEW_REPAIR_EXHAUSTED: last attempt {handback['status']}: "
                                               f"{str(handback.get('summary', ''))[:200]}")
            _record_slice_outcome(lane, packet["repair"]["publication"], "PR_BLOCKED")
        _save_publications(lane, items)
    lane.save(current, ledger)
    lane.active_path.unlink(missing_ok=True)
    if packet.get("slice"):
        summary = str(handback.get("summary", ""))
        result = (outcome["status"] if outcome["status"] == "ACCEPTED_LOCAL" else "SUPERVISOR_RECHECK_FAILED"
                  if handback["status"] == "PASS" else summary[:200] if summary.startswith(backlog.EXTERNAL)
                  else f"{handback['status']}: {summary}"[:200])
        failed_source_fingerprint = None
        if handback["status"] == "FAIL" and handback.get("changed_paths"):
            # Only record source bytes when the worktree is still available.
            # This is passive evidence, not a new execution or dispatch gate.
            from .iron_god_failed_source import read_candidate_sources, source_fingerprint

            try:
                candidate_files = read_candidate_sources(Path(packet["worktree"]), handback["changed_paths"])
                failed_source_fingerprint = source_fingerprint(candidate_files)
            except (OSError, ValueError, TypeError, KeyError):
                pass
        backlog.record_item(lane.root, packet["slice"]["id"], packet_id=packet_id, outcome=result,
                            failed_source_fingerprint=failed_source_fingerprint)
    try:  # observed facts for planners; a memory failure never affects the lane
        recorded = result if packet.get("slice") else str(outcome["status"])
        development_memory.record_attempt(lane.root, packet, handback, recorded)
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError):
        pass
    tried =(f"{slice_plan['function_name']} in {slice_plan['module_path']}" if slice_plan.get("function_name")
             else None)
    working_memory.record(lane.state_root, repo, "TURN_" + outcome["status"], packet_id=packet_id,
                          outcome=outcome["status"], note=note, attempt=tried,
                          blocker=None if outcome["status"] == "ACCEPTED_LOCAL" else
                          f"{handback['status']}: {handback.get('summary', '')}", active=None)
    return _report(lane, outcome)


def advance_product_lane(
    state_root: Path,
    release_repo: Path,
    *,
    supervisor: dict[str, Any],
    release_sha: str,
    allow_dispatch: bool,
    decide: Callable[[], Any] | None = None,
    spawn: Callable[..., dict[str, Any]] = spawn_product_worker,
    alive: Callable[[dict[str, Any]], bool] = worker_alive,
    stop: Callable[[dict[str, Any]], None] = stop_worker,
    resources: Callable[[], dict[str, Any]] = default_resources,
    lease_pool: RuntimeLeasePool | None = None,
    capability: Callable[[dict[str, Any]], str | None] | None = None,
    github: GitHub | None = None,
    sync: Callable[[ProductLane, str], tuple[Path, str]] = sync_clone,
    owned_paths: Callable[[str], list[str]] = open_pr_paths,
    gates: Callable[[Path, str, list[str]], str | None] | None = None,
    memory_root: Path | None = None,
    rng: random.Random | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Advance the product rotation by one fail-closed step."""
    from .iron_god_canon_lane import load_policy

    lane = ProductLane(state_root)
    lane.root.mkdir(parents=True, exist_ok=True)
    config = rotation_config(policy if policy is not None else load_policy(release_repo))
    if config is None:
        return {"status": "PORTFOLIO_ROTATION_DISABLED"}
    pool = lease_pool if lease_pool is not None else default_lease_pool()
    if decide is None:
        decision = acknowledge_and_evaluate(owner_controller.control_root(state_root),
                                            supervisor_identity(supervisor), release_sha=release_sha)
    else:
        decision = decide()
    ledger = lane.ledger()
    active = lane.active()
    if not getattr(decision, "permitted", False):
        reasons = list(getattr(decision, "reasons", ()) or ("EXECUTION_AUTHORITY_DENIED",))
        if active:
            stop(active["worker"])
            _release(pool, active)
            record = ledger["packets"].get(active["packet_id"])
            if record and record["state"] in ACTIVE_STATES:
                lane.save(transition_packet(ledger, active["packet_id"], state="CANCELLED",
                                            evidence_ref="owner-controller:" + ",".join(reasons)[:200]), ledger)
            lane.active_path.unlink(missing_ok=True)
            if active.get("repair_of"):
                # The owner taking control is not a failed repair: the publication waits for the next
                # authorized pass with its attempt refunded, instead of being stranded in REPAIRING.
                items = lane.publications()["items"]
                pub = items.get(active["repair_of"])
                if pub is not None and pub.get("state") == "REPAIRING":
                    pub.update(state="REPAIR_PENDING", repair_attempts=max(0, int(pub.get("repair_attempts", 1)) - 1))
                    _save_publications(lane, items)
            working_memory.record(state_root, active["repository"], "WORKER_STOPPED_AUTHORITY_REVOKED",
                                  packet_id=active["packet_id"], outcome="CANCELLED", active=None)
            return _report(lane, {"status": "WORKER_STOPPED_AUTHORITY_REVOKED", "packet_id": active["packet_id"],
                                  "worktree_preserved": active.get("worktree")})
        return _report(lane, {"status": "WAITING_FOR_OWNER_AUTHORITY", "reasons": reasons})
    merge = "review" if config.get("merge") == "review" else "auto"
    published = advance_publications(lane, release_repo, github=github or GitHub(release_repo), memory_root=memory_root,
                                     merge=merge)
    if active:
        if alive(active["worker"]):
            record = ledger["packets"].get(active["packet_id"])
            if record and record["state"] == "DISPATCHED":
                lane.save(transition_packet(ledger, active["packet_id"], state="IMPLEMENTING"), ledger)
            return _report(lane, {"status": "IMPLEMENTING", "packet_id": active["packet_id"],
                                  "repository": active["repository"], "publications": published})
        result = harvest(lane, release_repo, ledger, active, gates=gates or default_gates(release_repo))
        _release(pool, active)
        return result
    if not allow_dispatch:
        return _report(lane, {"status": "WAITING_FOR_COMMISSIONING_LANE", "publications": published})
    refused = (capability or (lambda s: capability_gate(load_profile(state_root), s)))(
        {"capability_level": int(config.get("capability_level", 3))})
    if refused:
        return _report(lane, {"status": "WAITING_FOR_CALIBRATION", "reason": refused})
    publications = lane.publications()["items"]
    stranded = [pid for pid, item in publications.items() if item["state"] == "REPAIRING"]
    for pid in stranded:  # no worker is active, so a REPAIRING publication lost its repair (e.g. a crash)
        item = publications[pid]
        if int(item.get("repair_attempts", 0)) < MAX_REVIEW_REPAIRS:
            item.update(state="REPAIR_PENDING")
        else:
            item.update(state="BLOCKED", reason="REVIEW_REPAIR_EXHAUSTED: last repair worker ended without a handback")
            _record_slice_outcome(lane, pid, "PR_BLOCKED")
    if stranded:
        _save_publications(lane, publications)
    pending_repair = next(((pid, item) for pid, item in sorted(publications.items())
                           if item["state"] == "REPAIR_PENDING"), None)
    if pending_repair is not None:  # finishing an open pull request comes before starting new work
        pid, item = pending_repair
        admission = resources()
        if not admission.get("admitted"):
            return _report(lane, {"status": "WAITING_FOR_RESOURCES", "repository": item["repository"],
                                  "admission": admission})
        try:
            lease = pool.acquire(WORKER_LEASE_RUNTIME, WORKER_LEASE_ID, owner_pid=os.getpid())
        except Exception as exc:  # Never share the local model.
            return _report(lane, {"status": "WAITING_FOR_RUNTIME_LEASE", "repository": item["repository"],
                                  "detail": f"{type(exc).__name__}: {str(exc)[:200]}"})
        try:
            result = dispatch_repair(lane, release_repo, ledger, pid, publications, release_sha=release_sha,
                                     spawn=spawn, owned_paths=owned_paths, lease_id=lease.lease_id)
        except BaseException:
            pool.release(WORKER_LEASE_ID, lease.lease_id)
            raise
        if result["status"] != "DISPATCHED":
            pool.release(WORKER_LEASE_ID, lease.lease_id)
        return result
    repos = product_repositories(release_repo, config)
    busy = {item["repository"] for item in publications.values()
            if item["state"] in PUBLICATION_OPEN | REPAIR_STATES}
    # A repository whose own preflight fails on unchanged main cannot judge a slice; it waits
    # until an owner-held readiness check (product trial --preflight-only) passes again.
    repo_readiness = lane._json(lane.root / "readiness.json", {"repositories": {}}).get("repositories", {})
    busy |= {repo for repo, row in repo_readiness.items() if row.get("ready") is False}
    items: dict[str, dict[str, Any]] = {}
    if config.get("planning") == "backlog":  # roadmap-driven: only approved slices, never self-invented ones
        approved, _rejected = backlog.load_backlog(release_repo)
        state = backlog.load_state(lane.root)
        level = int(config.get("development_level", 1))
        if config.get("roadmap_refill", True):
            approved = refill_from_roadmaps(
                lane, repos, busy, approved, state, level, sync, owned_paths,
                capable={"enabled": config.get("capable_builder") is True,
                         "daily_limit": int(config.get("capable_builder_daily_limit", capable_builder.DEFAULT_DAILY_LIMIT))},
                record_progress=config.get("roadmap_progress", True) is True, release=(release_repo, release_sha),
                draft_locally=config.get("local_draft_author", True) is True)
        for name in repos:
            chosen = backlog.next_item(approved, state, name, max_level=level)
            if chosen is not None:
                items[name] = chosen
        busy |= {name for name in repos if name not in items}
    repo = next_repository(lane, repos, busy, rng)
    if repo is not None and local_drafter.drafting_in_progress(lane.root):
        # the single local model is planning a roadmap slice; start no builder beside it
        return _report(lane, {"status": "WAITING_FOR_LOCAL_DRAFT", "repository": repo})
    if repo is None:
        idle = "IDLE_NO_APPROVED_BACKLOG_ITEM" if config.get("planning") == "backlog" and not items else (
            "IDLE_ALL_REPOSITORIES_AWAITING_CI")
        return _report(lane, {"status": idle, "publications": published})
    admission = resources()
    if not admission.get("admitted"):
        return _report(lane, {"status": "WAITING_FOR_RESOURCES", "repository": repo, "admission": admission})
    try:
        lease = pool.acquire(WORKER_LEASE_RUNTIME, WORKER_LEASE_ID, owner_pid=os.getpid())
    except Exception as exc:  # Never share the local model.
        return _report(lane, {"status": "WAITING_FOR_RUNTIME_LEASE", "repository": repo,
                              "detail": f"{type(exc).__name__}: {str(exc)[:200]}"})
    try:
        result = dispatch(lane, release_repo, ledger, repo, release_sha=release_sha, spawn=spawn, sync=sync,
                          owned_paths=owned_paths, lease_id=lease.lease_id, item=items.get(repo))
    except BaseException:
        pool.release(WORKER_LEASE_ID, lease.lease_id)
        raise
    if result["status"] != "DISPATCHED":
        pool.release(WORKER_LEASE_ID, lease.lease_id)
    return result


def repository_unsupported(clone: Path, head: str) -> str | None:
    """Why the Python product lane cannot build this repository at all (e.g. an Unreal/C++ game), else None."""
    roots = scope_envelope(clone, head)
    return None if roots and python_packages(clone, head, roots) else "NO_PYTHON_PACKAGE_FOR_LOCAL_BUILDER"


def hand_to_capable_builder(lane: ProductLane, name: str, report: dict[str, Any], capable: dict[str, Any]) -> None:
    """Dispatch CAPABLE-routed requests within budget and record every outcome; failures never stop the rotation."""
    for request in report.get("capable_requests", []):
        try:
            result = capable_builder.dispatch_request(request, lane_root=lane.root, enabled=capable["enabled"],
                                                      daily_limit=capable["daily_limit"])
        except Exception as exc:  # a routine API error is a recorded blocker, not a crash
            result = {"dispatched": False, "reason": f"CAPABLE_BUILDER_ERROR: {type(exc).__name__}"}
        kind = "CAPABLE_DISPATCHED" if result.get("dispatched") else "CAPABLE_NOT_DISPATCHED"
        roadmap_refill.log_event(lane.root, kind, name, request["id"],
                                 {key: value for key, value in result.items() if key != "dispatched"})
        working_memory.record(lane.state_root, name, kind, detail=f"{request['id']}: {result.get('reason')}",
                              outcome=None if result.get("dispatched") else "BLOCKED")


def spawn_draft_author(release_repo: Path, release_sha: str, request_file: Path, clone: Path, base: str, result: Path,
                       log_path: Path) -> int:
    """Launch the local draft author like a product worker: release-bound, its own process."""
    env = {**os.environ, "PYTHONPATH": str(Path(release_repo) / "src"), "IRON_GOD_ACCEPTED_SHA": release_sha}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(release_repo) / "scripts" / "iron_god_draft_author.py"), "--request",
             str(request_file), "--clone", str(clone), "--base", base, "--result", str(result)],
            cwd=str(release_repo), env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=flags)
    return process.pid


def draft_with_local_builder(lane: ProductLane, name: str, report: dict[str, Any], clone: Path, head: str,
                             release: tuple[Path, str] | None) -> None:
    """Harvest and start local draft jobs for LOCAL-routed Level 1 requests; outcomes are logged, never fatal."""
    if release is None:
        return
    release_repo, release_sha = release
    try:
        outcomes = local_drafter.advance(
            lane.root, report.get("local_requests", []), clone, head,
            spawn=lambda request_file, tree, base, out: spawn_draft_author(
                release_repo, release_sha, request_file, tree, base, out, lane.reports / f"draft-{out.stem}.log"),
            may_start=lane.active() is None,
            extra={"attempted_functions": sorted(working_memory.attempted_functions(lane.state_root, name))})
    except Exception as exc:  # drafting is best-effort; other repositories and the rotation continue
        outcomes = [{"id": name, "status": "ERROR", "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}]
    for outcome in outcomes:
        roadmap_refill.log_event(lane.root, "LOCAL_DRAFT_" + outcome["status"], name, outcome["id"], outcome)
        working_memory.record(lane.state_root, name, "LOCAL_DRAFT_" + outcome["status"],
                              detail=f"{outcome['id']}: {outcome.get('reason', '')}"[:200],
                              outcome="BLOCKED" if outcome["status"] in {"FAILED", "ERROR"} else None)


def spawn_progress_preflight(release_repo: Path, release_sha: str, worktree: Path, result: Path,
                              log_path: Path) -> int:
    """Launch the progress preflight runner exactly like a product worker: release-bound, its own process."""
    env = {**os.environ, "PYTHONPATH": str(Path(release_repo) / "src"), "IRON_GOD_ACCEPTED_SHA": release_sha}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(release_repo) / "scripts" / "iron_god_progress_preflight.py"),
             "--worktree", str(worktree), "--result", str(result)],
            cwd=str(release_repo), env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=flags)
    return process.pid


def record_roadmap_progress(lane: ProductLane, name: str, clone: Path, head: str, items: list[dict[str, Any]],
                            state: dict[str, Any], release: tuple[Path, str] | None = None) -> None:
    """Advance the mechanical graph-progress job for ``name``; outcomes are logged, never fatal.

    The repository's native preflight runs in its own process and only while no worker is active, so the supervisor
    heartbeat never stalls and heavy checks stay serial. Without a release identity nothing is started.
    """
    if release is None:
        return
    release_repo, release_sha = release
    try:
        result = roadmap_progress.record_progress(
            lane.root, lane.state_root, name, clone, head, items=items, state=state,
            publications=lane.publications()["items"], workspace=lane.workspaces / "progress",
            spawn=lambda tree, out: spawn_progress_preflight(release_repo, release_sha, tree, out,
                                                             lane.reports / f"progress-{out.stem}.log"),
            may_start=lane.active() is None)
    except Exception as exc:  # recording progress is best-effort; the overlay already lets the engine continue
        result = {"status": "ERROR", "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
    if result["status"] in {"OPENED", "BLOCKED", "ERROR"}:
        roadmap_refill.log_event(lane.root, "PROGRESS_" + result["status"], name, result.get("branch") or name, result)
        working_memory.record(lane.state_root, name, "ROADMAP_PROGRESS_" + result["status"],
                              detail=str(result.get("pr") or result.get("reason") or ""),
                              outcome=None if result["status"] == "OPENED" else "BLOCKED")


def refill_from_roadmaps(lane: ProductLane, repos: list[str], busy: set[str], static: list[dict[str, Any]],
                         state: dict[str, Any], level: int, sync: Callable[[ProductLane, str], tuple[Path, str]],
                         owned_paths: Callable[[str], list[str]],
                         capable: dict[str, Any] | None = None, record_progress: bool = False,
                         release: tuple[Path, str] | None = None, draft_locally: bool = False) -> list[dict[str, Any]]:
    """Backlog plus runtime-admitted roadmap items; a repository with no dispatchable item gets one planning pass.

    Owner delegation 2026-11-01 (issue #594): the owner-approved roadmap authorizes development, so an
    exhausted backlog is refilled from it instead of idling. A failure here never stops the rotation.
    """
    approved = [*static, *roadmap_refill.admitted_items(lane.root, static)]
    capable = capable or {"enabled": False, "daily_limit": capable_builder.DEFAULT_DAILY_LIMIT}
    if capable["enabled"]:
        try:
            capable_builder.reconcile(lane.root)
        except Exception as exc:  # observing GitHub is best-effort; the attempt cap still bounds retries
            working_memory.record(lane.state_root, "<portfolio>", "CAPABLE_RECONCILE_FAILED", outcome="SKIPPED",
                                  blocker=f"{type(exc).__name__}: {str(exc)[:200]}")
    for name in repos:
        if name in busy or backlog.next_item(approved, state, name, max_level=level) is not None:
            continue
        if not roadmap_refill.due(lane.root, name) and not (draft_locally and local_drafter.has_result_ready(lane.root)):
            continue
        try:
            clone, head = sync(lane, name)
            report = roadmap_refill.refill_repository(
                lane.root, lane.state_root, name, clone, head, items=approved, state=state,
                reserved={path.replace("\\", "/") for path in owned_paths(name)}, max_level=level,
                unsupported=repository_unsupported)
        except Exception as exc:  # planning is best-effort; dispatch of other work continues
            working_memory.record(lane.state_root, name, "ROADMAP_REFILL_FAILED", outcome="SKIPPED",
                                  blocker=f"{type(exc).__name__}: {str(exc)[:200]}")
            continue
        hand_to_capable_builder(lane, name, report, capable)
        if draft_locally:
            draft_with_local_builder(lane, name, report, clone, head, release)
        if record_progress:
            record_roadmap_progress(lane, name, clone, head, approved, state, release)
        working_memory.record(lane.state_root, name, "ROADMAP_REFILL", detail=roadmap_refill.summarize(report),
                              outcome="DIRECTION_REQUIRED" if report["direction_required"] and not report["requests"] else None)
        approved = [*static, *roadmap_refill.admitted_items(lane.root, static)]
    return approved


def dispatch(lane: ProductLane, release_repo: Path, ledger: dict[str, Any], repo: str, *, release_sha: str,
             spawn: Callable[..., dict[str, Any]], sync: Callable[[ProductLane, str], tuple[Path, str]],
             owned_paths: Callable[[str], list[str]], lease_id: str,
             item: dict[str, Any] | None = None) -> dict[str, Any]:
    name = repo.split("/", 1)[-1]
    packet_id = f"PRODUCT-{name.upper()}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    try:
        clone, base = sync(lane, repo)
        avoid = owned_paths(repo)
    except Exception as exc:  # This repository is skipped for this turn; the rotation still advances.
        _advance_cursor(lane, repo, packet_id)
        working_memory.record(lane.state_root, repo, "SYNC_FAILED", packet_id=packet_id, outcome="SKIPPED",
                              blocker=f"{type(exc).__name__}: {str(exc)[:200]}")
        return _report(lane, {"status": "REPOSITORY_SYNC_FAILED", "repository": repo,
                              "detail": f"{type(exc).__name__}: {str(exc)[:300]}"})
    held = sorted(backlog.edit_files(item) & set(avoid)) if item is not None else []
    if held:
        # 2026-10-28: KEC-6's one attempt was spent refusing a file under another developer's open pull
        # request. A conflict is not an attempt: the item waits, and is re-checked on its next turn.
        _advance_cursor(lane, repo, packet_id)
        backlog.record_block(lane.root, item["id"], outcome=f"BLOCKED_CONFLICT: open pull request on {', '.join(held)}")
        return _report(lane, {"status": "BLOCKED_CONFLICT", "repository": repo, "backlog_item": item["id"],
                              "files": held})
    roots = scope_envelope(clone, base)
    if not roots or not python_packages(clone, base, roots):
        # Product slices add a module to an existing Python package; a repository without
        # one (e.g. an Unreal/C++ game with loose tools) is not eligible for this slice type.
        _advance_cursor(lane, repo, packet_id)
        record_readiness(lane, repo, base, ready=False, reason="NO_PYTHON_PACKAGE")
        return _report(lane, {"status": "NOT_ELIGIBLE_NO_PYTHON_PACKAGE", "repository": repo})
    worktree = lane.worktrees / packet_id
    branch = f"iron-god/{name.lower()}/{packet_id.lower()}"
    added = _git(clone, "worktree", "add", "-b", branch, str(worktree), base)
    if added.returncode != 0:
        _advance_cursor(lane, repo, packet_id)
        return _report(lane, {"status": "WORKTREE_PROVISION_FAILED", "repository": repo,
                              "detail": added.stderr[-300:]})
    packet = build_product_packet(repo, packet_id=packet_id, base=base, worktree=worktree, branch=branch,
                                  roots=roots, avoid=avoid, memory_text=working_memory.excerpt(lane.state_root, repo),
                                  attempted=working_memory.attempted_functions(lane.state_root, repo),
                                  project=memory_project(release_repo, repo), item=item)
    updated = dispatch_canonical_slice(ledger, packet, transport="local_idle_worker")
    lane.save(updated, ledger)
    packet_path = lane.packets / f"{packet_id}.json"
    handback_path = lane.handbacks / f"{packet_id}.json"
    atomic_json(packet_path, packet)
    handback_path.unlink(missing_ok=True)
    worker = spawn(release_repo, packet_path, handback_path, release_sha=release_sha,
                   log_path=lane.reports / f"{packet_id}-worker.log")
    atomic_json(lane.active_path, {"packet_id": packet_id, "repository": repo, "worker": worker,
                                   "worktree": str(worktree), "dispatched_at_utc": _now(), "base_sha": base,
                                   "lease_id": lease_id})
    _advance_cursor(lane, repo, packet_id)
    if item is not None:
        backlog.record_item(lane.root, item["id"], packet_id=packet_id, outcome="DISPATCHED")
    working_memory.record(lane.state_root, repo, "DISPATCHED", packet_id=packet_id, detail=f"base {base}",
                          active={"packet_id": packet_id, "repository": repo, "worker_pid": worker["pid"],
                                  "started_at_utc": _now()})
    return _report(lane, {"status": "DISPATCHED", "packet_id": packet_id, "repository": repo, "base_sha": base,
                          "worker_pid": worker["pid"], "scope": roots,
                          "backlog_item": item["id"] if item is not None else None})


def dispatch_repair(lane: ProductLane, release_repo: Path, ledger: dict[str, Any], publication_id: str,
                    publications: dict[str, Any], *, release_sha: str, spawn: Callable[..., dict[str, Any]],
                    owned_paths: Callable[[str], list[str]], lease_id: str) -> dict[str, Any]:
    """Dispatch one bounded review repair: a directed edit of the slice's module on the exact PR head."""
    item = publications[publication_id]
    repo, head = item["repository"], item["head_sha"]
    clone = lane.clone(repo)
    attempt = int(item.get("repair_attempts", 0)) + 1
    # Unique (a cancelled repair keeps its worktree) but short: the branch already carries the packet id,
    # and a long ref under canon-workspaces/products/<repo>/.git/refs/heads hit Windows MAX_PATH (2026-10-31).
    stamp = datetime.now(UTC).strftime("%d%H%M%S")
    repair_id = f"{publication_id}-R{attempt}-{stamp}"
    # local-only name: the repaired head is pushed to the pull request's own branch (item["branch"])
    branch = f"igr/{hashlib.sha256(publication_id.encode()).hexdigest()[:8]}-{attempt}-{stamp}"
    worktree = lane.worktrees / repair_id

    def block(reason: str) -> dict[str, Any]:
        item.update(state="BLOCKED", reason=reason[:300], repair_attempts=attempt)
        _save_publications(lane, publications)
        _record_slice_outcome(lane, publication_id, "PR_BLOCKED")
        working_memory.record(lane.state_root, repo, "REVIEW_REPAIR_BLOCKED", packet_id=publication_id,
                              outcome="BLOCKED", blocker=reason[:200])
        return _report(lane, {"status": "REVIEW_REPAIR_NOT_DISPATCHED", "packet_id": publication_id,
                              "reason": reason[:300]})

    module, test = item.get("repair_module"), item.get("repair_test")
    if not module or not test or not item.get("findings"):
        return block("REPAIR_RECORD_INCOMPLETE")
    if _git(clone, "cat-file", "-e", f"{head}^{{commit}}").returncode != 0:
        _git(clone, "fetch", "-q", "origin", item["branch"])
        if _git(clone, "cat-file", "-e", f"{head}^{{commit}}").returncode != 0:
            return block("REPAIR_HEAD_UNAVAILABLE")
    added = _git(clone, "worktree", "add", "-b", branch, str(worktree), head)
    if added.returncode != 0:
        return block("REPAIR_WORKTREE_PROVISION_FAILED: " + added.stderr[-200:])
    original = json.loads((lane.packets / f"{publication_id}.json").read_text(encoding="utf-8"))
    avoid = [p for p in owned_paths(repo) if p not in {module, test}]
    packet = build_product_packet(repo, packet_id=repair_id, base=head, worktree=worktree, branch=branch,
                                  roots=original["paths"], avoid=avoid, memory_text="", project=original["project"])
    objective = (f"Repair the code-review findings on pull request #{item['pr']['number']} by editing only "
                 f"{module}; the slice's tests in {test} stay unchanged and must keep passing.")
    packet["task"] = objective
    packet["planning"].update(
        objective=objective,
        acceptance_criteria=[f"only {module} changes", f"the slice's tests in {test} pass unchanged",
                             "the builder change gates pass", "the canonical preflight passes at the exact head",
                             "fresh CI passes and review settles with no unresolved finding before merge"],
        handoff_instructions="Address the recorded review findings in the module only, run the canonical preflight, "
                             "and hand back exact evidence.")
    packet["repair"] = {"publication": publication_id, "attempt": attempt, "pr": item["pr"], "module_path": module,
                        "test_path": test, "findings": item.get("findings", []), "slice": original.get("slice")}
    updated = dispatch_canonical_slice(ledger, packet, transport="local_idle_worker")
    lane.save(updated, ledger)
    packet_path = lane.packets / f"{repair_id}.json"
    handback_path = lane.handbacks / f"{repair_id}.json"
    atomic_json(packet_path, packet)
    handback_path.unlink(missing_ok=True)
    worker = spawn(release_repo, packet_path, handback_path, release_sha=release_sha,
                   log_path=lane.reports / f"{repair_id}-worker.log")
    atomic_json(lane.active_path, {"packet_id": repair_id, "repository": repo, "worker": worker,
                                   "worktree": str(worktree), "dispatched_at_utc": _now(), "base_sha": head,
                                   "lease_id": lease_id, "repair_of": publication_id})
    item.update(state="REPAIRING", repair_attempts=attempt, repair_packet=repair_id)
    _save_publications(lane, publications)
    working_memory.record(lane.state_root, repo, "REVIEW_REPAIR_DISPATCHED", packet_id=repair_id,
                          detail=f"attempt {attempt} for pull request #{item['pr']['number']}",
                          active={"packet_id": repair_id, "repository": repo, "worker_pid": worker["pid"],
                                  "started_at_utc": _now()})
    return _report(lane, {"status": "DISPATCHED", "packet_id": repair_id, "repository": repo, "base_sha": head,
                          "worker_pid": worker["pid"], "repair_of": publication_id})


def _release(pool: RuntimeLeasePool, active: dict[str, Any]) -> None:
    lease_id = active.get("lease_id")
    if lease_id:
        try:
            pool.release(WORKER_LEASE_ID, lease_id)
        except Exception:
            pass  # A stale generation is reclaimed by the pool's dead-owner recovery.
