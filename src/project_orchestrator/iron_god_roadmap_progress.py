"""Record merged roadmap slices in the repository's own capability graph (issue #594).

The continuation overlay (``iron_god_roadmap_refill.with_lane_progress``) lets the engine keep going the moment a
slice merges. This module makes that progress *durable and visible*: it opens one small pull request per repository
that sets ``state: VERIFIED_COMPLETE`` and a ``verification`` list (the merged pull request URL and merge SHA) on the
slices the engine can prove were merged, so completed work is never re-planned after a restart and the graph the
owner reads is current.

The edit is mechanical and checked before anything is published:

* the graph file must already be in the canonical ``json.dumps(indent=2)`` form (otherwise the edit would reformat
  the whole file, so it is refused as a blocker);
* after the edit, only ``state`` and ``verification`` of the named slices may differ from the original, and the
  result must still pass the capability-graph validator;
* a capability is never marked complete here: that needs product-level evidence.

Publication follows the repository's own rule (exact-head native preflight BEFORE a pull request exists) as a
persisted, resumable sequence, one small job file per branch::

    PREPARED -> VERIFYING -> VERIFIED -> PUSHED -> PUBLISHED        (or FAILED, with the reason)

The heavy preflight runs in a separate process (``scripts/iron_god_progress_preflight.py``, the product worker's own
environment and supervised runtime) so the supervisor heartbeat never stalls, and only when the lane is idle. Nothing
is pushed until the preflight passed for the exact commit that will be pushed. A step interrupted by a crash, an
outage or a failed ``gh`` call resumes from where it stopped, and an existing branch is never taken as proof of a
pull request: ``PUSHED`` always asks GitHub whether the pull request exists before creating it.

The pull request is an ordinary one authored by the owner's ``gh`` login; the existing portfolio green-PR merger
merges it once the repository's checks pass. No new merge path is introduced.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from scripts.project_capability_graph import validate

from . import iron_god_capable_builder as capable_builder
from . import iron_god_roadmap_refill as refill
from .state_io import atomic_json

BRANCH_PREFIX = "iron-god/roadmap-progress/"
TITLE_PREFIX = "Iron God roadmap progress"
PREFLIGHT_TIMEOUT_S = 7200
FINAL = {"PUBLISHED", "FAILED"}


def _publications_for(item_row: dict[str, Any], publications: dict[str, Any]) -> list[str]:
    evidence = []
    for packet in item_row.get("packets", []):
        pub = publications.get(packet) or {}
        if pub.get("state") == "MERGED" and (pub.get("pr") or {}).get("url"):
            evidence.append(f"{pub['pr']['url']} merged as {str(pub.get('merged_sha', ''))[:12] or 'unrecorded'}")
    return evidence


def completed_slices(repository: str, payload: dict[str, Any], items: list[dict[str, Any]], state: dict[str, Any],
                     publications: dict[str, Any], lane_root: Path) -> list[dict[str, Any]]:
    """Slices provably merged that the graph still shows open, each with its evidence."""
    delivered = capable_builder.merged_request_ids(lane_root)
    pull_requests = {capable_builder.request_of(row): row.get("pr_url") for row in capable_builder._rows(
        capable_builder.dispatch_state(lane_root)) if row.get("pr_state") == "MERGED"}
    found = []
    for capability in payload.get("capabilities", []):
        slices = capability.get("slices") if isinstance(capability, dict) else None
        for row in slices if isinstance(slices, list) else []:
            if not isinstance(row, dict) or row.get("state") == "VERIFIED_COMPLETE":
                continue
            base = refill.item_id(repository, str(capability.get("id")), str(row.get("id")))
            status, ident = refill._slice_status(base, items, state, frozenset(delivered))
            if status != "DONE":
                continue
            outcome = state.get("items", {}).get(ident, {})
            evidence = _publications_for(outcome, publications)
            if not evidence and pull_requests.get(ident):
                evidence = [f"{pull_requests[ident]} merged (capable-builder pull request)"]
            if not evidence:
                evidence = [f"lane record for {ident}: {outcome.get('outcome', 'completed')}"]
            found.append({"capability": capability["id"], "slice": row["id"], "evidence": evidence})
    return found


class ProgressRefused(RuntimeError):
    """The mechanical edit cannot be made safely; the reason is recorded as a blocker, nothing is published."""


def apply_completions(graph_text: str, completions: list[dict[str, Any]]) -> str:
    """The graph file text with only the named slices' state and verification changed, or ProgressRefused."""
    newline = "\r\n" if "\r\n" in graph_text else "\n"
    normalized = graph_text.replace("\r\n", "\n")
    original = json.loads(normalized)
    if json.dumps(original, indent=2) + "\n" != normalized:
        raise ProgressRefused("GRAPH_FORMAT_NOT_CANONICAL")
    edited = json.loads(normalized)
    wanted = {(c["capability"], c["slice"]): c["evidence"] for c in completions}
    touched = set()
    for capability in edited["capabilities"]:
        for row in capability.get("slices", []):
            key = (capability["id"], row["id"])
            if key in wanted:
                row["state"] = "VERIFIED_COMPLETE"
                row["verification"] = list(wanted[key])
                touched.add(key)
    if touched != set(wanted):
        raise ProgressRefused("SLICE_NOT_FOUND_IN_GRAPH")
    # Prove nothing else changed: undo the intended edits on a copy and compare with the original.
    check = json.loads(json.dumps(edited))
    for capability, source in zip(check["capabilities"], original["capabilities"], strict=True):
        for row, was in zip(capability.get("slices", []), source.get("slices", []), strict=True):
            if (capability["id"], row["id"]) in wanted:
                row["state"], row["verification"] = was.get("state"), was.get("verification")
                if "verification" not in was:
                    row.pop("verification")
    if check != original:
        raise ProgressRefused("EDIT_TOUCHED_MORE_THAN_THE_NAMED_SLICES")
    if validate(edited)["status"] == "DIRECTION_REQUIRED":
        raise ProgressRefused("EDITED_GRAPH_FAILS_VALIDATION")
    return (json.dumps(edited, indent=2) + "\n").replace("\n", newline)


def branch_name(repository: str, completions: list[dict[str, Any]], head: str) -> str:
    """Stable per completion set and base commit: a retry on a newer main is a new branch, never a clobber."""
    digest = hashlib.sha256(json.dumps(sorted((c["capability"], c["slice"]) for c in completions)).encode()).hexdigest()[:10]
    return f"{BRANCH_PREFIX}{repository.split('/', 1)[-1].lower()}-{digest}-{head[:8]}"


def render_body(completions: list[dict[str, Any]], source: str, head: str) -> str:
    lines = [f"Mechanical update of `{source}` at `{head[:12]}`: records slices the engine merged, so completed work is "
             "not re-planned after a restart.", ""]
    for c in completions:
        lines.append(f"- `{c['capability']}` / `{c['slice']}`: " + "; ".join(c["evidence"]))
    lines += ["", "Only `state` and `verification` of the listed slices changed; the file was verified to be in canonical "
              "form before and after, the graph still passes the capability-graph validator, and the repository's native "
              "preflight passed at this exact commit before it was pushed. No capability is marked complete here."]
    return "\n".join(lines)


def gh_publisher(repository: str, branch: str, title: str, body: str) -> str:
    out = subprocess.run(["gh", "pr", "create", "--repo", repository, "--base", "main", "--head", branch,
                          "--title", title, "--body", body], capture_output=True, text=True, check=False, timeout=120)
    if out.returncode != 0:
        raise RuntimeError("PROGRESS_PR_FAILED: " + out.stderr[-300:])
    return out.stdout.strip().splitlines()[-1]


def gh_pr_lookup(repository: str, branch: str) -> dict[str, Any] | None:
    """The pull request for ``branch`` (any state), None if GitHub says there is none. An unanswered query raises:
    not knowing is never treated as 'no pull request' (that would duplicate) or as 'published' (that would hide a gap)."""
    out = subprocess.run(["gh", "pr", "list", "--repo", repository, "--head", branch, "--state", "all", "--json",
                          "url,state", "--limit", "1"], capture_output=True, text=True, check=False, timeout=60)
    if out.returncode != 0:
        raise RuntimeError("PROGRESS_PR_LOOKUP_FAILED: " + out.stderr[-200:])
    rows = json.loads(out.stdout or "[]")
    return rows[0] if rows else None


def gh_pr_state(url: str) -> str | None:
    """OPEN, MERGED or CLOSED for a pull request URL; None when GitHub cannot be asked (treated as still open)."""
    out = subprocess.run(["gh", "pr", "view", url, "--json", "state", "--jq", ".state"], capture_output=True, text=True,
                         check=False, timeout=60)
    value = out.stdout.strip()
    return value if out.returncode == 0 and value in {"OPEN", "MERGED", "CLOSED"} else None


def spawn_preflight(worktree: Path, result: Path) -> int | None:
    """Default launcher: the native-preflight runner in its own process. The lane supplies a release-bound variant."""
    runner = Path(__file__).resolve().parents[2] / "scripts" / "iron_god_progress_preflight.py"
    result.parent.mkdir(parents=True, exist_ok=True)
    with result.with_suffix(".log").open("ab") as log:
        process = subprocess.Popen([sys.executable, str(runner), "--worktree", str(worktree), "--result", str(result)],
                                   stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    return process.pid


def _git(clone: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    # core.longpaths: a repository with deep tracked paths (Project-Everward's prototype captures) cannot otherwise be
    # checked out below a long workspace path on Windows ("Filename too long").
    return subprocess.run(["git", "-C", str(clone), "-c", "core.longpaths=true", *args], capture_output=True, text=True,
                          check=False, timeout=timeout)


def _job_file(workspace: Path, branch: str) -> Path:
    return Path(workspace) / "jobs" / (branch.replace("/", "-") + ".json")


def _jobs(workspace: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((Path(workspace) / "jobs").glob("*.json")) if (Path(workspace) / "jobs").is_dir() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and "branch" in row:
            rows.append(row)
    return rows


def _save(workspace: Path, job: dict[str, Any]) -> None:
    atomic_json(_job_file(workspace, job["branch"]), job)


def verifying(workspace: Path) -> bool:
    """True while a progress preflight is running; other heavy local work waits for it."""
    return any(job["stage"] == "VERIFYING" for job in _jobs(workspace))


def _return_lease(job: dict[str, Any], release: Callable[[str], None] | None) -> None:
    lease, job["lease_id"] = job.get("lease_id"), None
    if lease and release is not None:
        try:
            release(lease)
        except Exception:  # a lease that is already gone must not lose the job record
            pass


def _fail(workspace: Path, job: dict[str, Any], reason: str, detail: str = "",
          release: Callable[[str], None] | None = None) -> dict[str, Any]:
    _return_lease(job, release)
    job.update(stage="FAILED", reason=reason, detail=detail[-400:])
    _save(workspace, job)
    return {"status": "BLOCKED", "reason": reason, "branch": job["branch"], "detail": detail[-400:]}


def settle_verification(workspace: Path, job: dict[str, Any], *, now: Callable[[], float],
                        release: Callable[[str], None] | None) -> dict[str, Any] | None:
    """VERIFYING -> VERIFIED or FAILED once the result is in or the run hung; None means settled and still going.

    Needs neither the repository clone nor GitHub, so it can run for every job on every turn.
    """
    branch = job["branch"]
    result_path = Path(job["result"])
    if not result_path.is_file():
        if now() - float(job.get("started_at", 0)) > PREFLIGHT_TIMEOUT_S + 600:
            return _fail(workspace, job, "PREFLIGHT_TIMED_OUT", release=release)
        return {"status": "VERIFYING", "branch": branch}
    try:
        verdict = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _fail(workspace, job, "PREFLIGHT_RESULT_UNREADABLE", release=release)
    if verdict.get("ok") is not True or verdict.get("head") != job["commit_sha"]:
        return _fail(workspace, job, "PREFLIGHT_FAILED", str(verdict.get("output_tail", "")), release=release)
    _return_lease(job, release)  # the heavy part is over; pushing and publishing need no model
    job["stage"] = "VERIFIED"
    _save(workspace, job)
    return None


def harvest(workspace: Path, *, now: Callable[[], float] = time.time,
            release: Callable[[str], None] | None = None) -> list[dict[str, Any]]:
    """Settle every verifying job (result in, or hung) whatever happens to its repository; returns the failures.

    A repository that cannot be synced must not leave a finished or hung job holding the shared runtime lease and the
    portfolio-wide exclusion: that would suppress executable work everywhere else.
    """
    failures = []
    for job in [row for row in _jobs(workspace) if row["stage"] == "VERIFYING"]:
        outcome = settle_verification(workspace, job, now=now, release=release)
        if outcome is not None and outcome["status"] == "BLOCKED":
            failures.append({**outcome, "repository": job["repository"]})
    return failures


def _advance(job: dict[str, Any], *, workspace: Path, publisher: Callable[[str, str, str, str], str],
             pr_lookup: Callable[[str, str], dict[str, Any] | None], spawn: Callable[[Path, Path], int | None],
             may_start: bool, now: Callable[[], float], reserve: Callable[[], str | None] | None = None,
             release: Callable[[str], None] | None = None) -> dict[str, Any]:
    tree, branch, repository = Path(job["worktree"]), job["branch"], job["repository"]
    while True:
        stage = job["stage"]
        if stage == "PREPARED":
            if not may_start:  # the lane is busy with a worker; heavy checks run serially
                return {"status": "WAITING_FOR_IDLE", "branch": branch}
            lease = reserve() if reserve is not None else None  # the shared runtime lease: heavy checks stay serial
            if reserve is not None and lease is None:
                return {"status": "WAITING_FOR_IDLE", "branch": branch}
            result = Path(workspace) / "results" / (branch.replace("/", "-") + ".json")
            result.unlink(missing_ok=True)
            try:
                pid = spawn(tree, result)
            except Exception:
                if lease and release is not None:
                    release(lease)
                raise
            job.update(stage="VERIFYING", started_at=now(), pid=pid, result=str(result), lease_id=lease)
            _save(workspace, job)
            return {"status": "VERIFYING", "branch": branch}
        if stage == "VERIFYING":
            outcome = settle_verification(workspace, job, now=now, release=release)
            if outcome is not None:
                return outcome
            continue
        if stage == "VERIFIED":
            if _git(tree, "rev-parse", "HEAD").stdout.strip() != job["commit_sha"]:
                return _fail(workspace, job, "VERIFIED_COMMIT_CHANGED")  # only the verified commit may ever be pushed
            pushed = _git(tree, "push", "origin", f"{branch}:refs/heads/{branch}")
            if pushed.returncode != 0:
                return {"status": "BLOCKED", "reason": "PUSH_FAILED", "branch": branch, "detail": pushed.stderr[-200:]}
            job["stage"] = "PUSHED"  # persisted BEFORE publishing so an interrupted publish resumes here
            _save(workspace, job)
            continue
        if stage == "PUSHED":
            existing = pr_lookup(repository, branch)  # a branch is not proof of a pull request, and vice versa
            url = existing["url"] if existing else publisher(repository, branch, job["title"], job["body"])
            job.update(stage="PUBLISHED", pr=url)
            _save(workspace, job)
            _git(Path(job["clone"]), "worktree", "remove", str(tree))  # our own scratch worktree; failure leaves it
            return {"status": "OPENED", "branch": branch, "pr": url, "slices": job["slices"]}
        return {"status": "BLOCKED", "reason": f"UNKNOWN_STAGE_{stage}", "branch": branch}


MAX_BRANCH_VARIANTS = 4  # the planned branch plus up to three numbered variants; never more


def tree_path(workspace: Path, branch: str) -> Path:
    """A short, stable scratch path for a branch: the full branch name would push deep repositories past MAX_PATH."""
    return Path(workspace) / "trees" / ("t-" + hashlib.sha256(branch.encode()).hexdigest()[:12])


def branch_variants(branch: str) -> list[str]:
    return [branch, *(f"{branch}-{n}" for n in range(2, MAX_BRANCH_VARIANTS + 1))]


def is_variant_of(candidate: str, branch: str) -> bool:
    suffix = candidate[len(branch) + 1:]
    return candidate == branch or (candidate.startswith(branch + "-") and suffix.isdigit())


def provision_worktree(clone: Path, workspace: Path, branch: str, head: str) -> tuple[str | None, Path | None, str]:
    """Create the scratch worktree for ``branch`` at ``head``; returns (branch used, tree, detail on failure).

    A failed earlier attempt can leave the branch behind (git creates the ref before it checks files out), and the
    engine used to retry the same name forever (Project-Everward, 2026-11-03: 31 identical refusals). A leftover branch
    that still points exactly at ``head`` holds no work, so it is reused; one that holds anything else is never touched
    and the next numbered variant is tried. Nothing is deleted or reset.
    """
    detail = "no branch variant could be provisioned"
    for name in branch_variants(branch):
        tree = tree_path(workspace, name)
        if tree.exists():
            continue  # an earlier attempt's scratch directory: left alone
        tip = _git(clone, "rev-parse", "--verify", "-q", f"refs/heads/{name}")
        if tip.returncode == 0 and tip.stdout.strip() != head:
            continue  # a branch with its own history: never reused, never touched
        args = ("worktree", "add", str(tree), name) if tip.returncode == 0 else ("worktree", "add", "-b", name, str(tree), head)
        added = _git(clone, *args)
        if added.returncode == 0:
            return name, tree, ""
        detail = added.stderr[-200:]
    return None, None, detail


def record_progress(lane_root: Path, state_root: Path, repository: str, clone: Path, head: str, *,
                    items: list[dict[str, Any]], state: dict[str, Any], publications: dict[str, Any],
                    workspace: Path, publisher: Callable[[str, str, str, str], str] = gh_publisher,
                    pr_lookup: Callable[[str, str], dict[str, Any] | None] = gh_pr_lookup,
                    spawn: Callable[[Path, Path], int | None] = spawn_preflight, may_start: bool = True,
                    now: Callable[[], float] = time.time, reserve: Callable[[], str | None] | None = None,
                    release: Callable[[str], None] | None = None,
                    pr_state: Callable[[str], str | None] = gh_pr_state) -> dict[str, Any]:
    """Advance (or start) the single progress job for ``repository``; never raises on a refusal."""
    context = {"workspace": Path(workspace), "publisher": publisher, "pr_lookup": pr_lookup, "spawn": spawn,
               "may_start": may_start, "now": now, "reserve": reserve, "release": release}
    unfinished = [job for job in _jobs(workspace) if job["repository"] == repository and job["stage"] not in FINAL]
    if unfinished:  # one open progress job per repository: finish it before looking at anything newer
        return _advance(unfinished[0], **context)
    for job in _jobs(workspace):  # one open progress pull request per repository: a second would conflict on the file
        if job["repository"] == repository and job["stage"] == "PUBLISHED" and job.get("pr_state") not in ("MERGED", "CLOSED"):
            observed = pr_state(str(job.get("pr", "")))
            if observed in ("MERGED", "CLOSED"):
                job["pr_state"] = observed
                _save(workspace, job)
            else:
                return {"status": "WAITING_FOR_PROGRESS_PR", "pr": job.get("pr"), "branch": job["branch"]}
    graph_file = Path(state_root) / "capability-graphs" / f"{repository.replace('/', '__')}.json"
    try:
        compiled = json.loads(graph_file.read_text(encoding="utf-8"))
        source = compiled["authority"]["source"]
    except (OSError, ValueError, KeyError):
        return {"status": "NO_COMPILED_GRAPH"}
    if compiled["authority"].get("exact_head") != head:
        return {"status": "GRAPH_NOT_AT_HEAD"}
    completions = completed_slices(repository, compiled, items, state, publications, lane_root)
    if not completions:
        return {"status": "NOTHING_TO_RECORD"}
    branch = branch_name(repository, completions, head)
    prior = next((job for job in _jobs(workspace) if is_variant_of(str(job["branch"]), branch)), None)
    if prior is not None:  # a final job for this exact set and base: published already, or failed for a recorded reason
        if prior["stage"] == "PUBLISHED":
            return {"status": "ALREADY_OPEN", "branch": branch, "pr": prior.get("pr")}
        return {"status": "BLOCKED", "reason": prior.get("reason", "PRIOR_ATTEMPT_FAILED"), "branch": branch}
    shown = _git(clone, "show", f"{head}:{source}")
    if shown.returncode != 0:
        return {"status": "BLOCKED", "reason": "GRAPH_FILE_UNAVAILABLE"}
    try:
        text = apply_completions(shown.stdout, completions)
    except ProgressRefused as exc:
        return {"status": "BLOCKED", "reason": str(exc)}
    used, tree, detail = provision_worktree(clone, workspace, branch, head)
    if used is None or tree is None:
        return {"status": "BLOCKED", "reason": "WORKTREE_PROVISION_FAILED", "branch": branch, "detail": detail}
    branch = used
    (tree / source).write_text(text, encoding="utf-8", newline="")
    changed = _git(tree, "diff", "--name-only").stdout.split()
    if changed != [source]:
        return {"status": "BLOCKED", "reason": "UNEXPECTED_FILES_CHANGED", "files": changed}
    title = f"{TITLE_PREFIX}: record {len(completions)} merged slice(s)"
    _git(tree, "add", source)
    committed = _git(tree, "-c", "user.name=Iron God", "-c", "user.email=iron-god@users.noreply.github.com",
                     "commit", "-q", "-m", title)
    if committed.returncode != 0:
        return {"status": "BLOCKED", "reason": "COMMIT_FAILED", "detail": committed.stderr[-200:]}
    job = {"repository": repository, "branch": branch, "base": head, "source": source, "clone": str(clone),
           "worktree": str(tree), "commit_sha": _git(tree, "rev-parse", "HEAD").stdout.strip(), "stage": "PREPARED",
           "slices": [c["slice"] for c in completions], "title": title, "body": render_body(completions, source, head)}
    _save(workspace, job)
    return _advance(job, **context)
