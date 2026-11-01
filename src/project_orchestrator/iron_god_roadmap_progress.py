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

The pull request is an ordinary one authored by the owner's ``gh`` login; the existing portfolio green-PR merger
merges it once the repository's checks pass. No new merge path is introduced.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from scripts.project_capability_graph import validate

from . import iron_god_capable_builder as capable_builder
from . import iron_god_roadmap_refill as refill

BRANCH_PREFIX = "iron-god/roadmap-progress/"
TITLE_PREFIX = "Iron God roadmap progress"


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
        for row in capability.get("slices", []) if isinstance(capability, dict) else []:
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


def branch_name(repository: str, completions: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256(json.dumps(sorted((c["capability"], c["slice"]) for c in completions)).encode()).hexdigest()[:10]
    return f"{BRANCH_PREFIX}{repository.split('/', 1)[-1].lower()}-{digest}"


def render_body(completions: list[dict[str, Any]], source: str, head: str) -> str:
    lines = [f"Mechanical update of `{source}` at `{head[:12]}`: records slices the engine merged, so completed work is "
             "not re-planned after a restart.", ""]
    for c in completions:
        lines.append(f"- `{c['capability']}` / `{c['slice']}`: " + "; ".join(c["evidence"]))
    lines += ["", "Only `state` and `verification` of the listed slices changed; the file was verified to be in canonical "
              "form before and after, and the graph still passes the capability-graph validator. No capability is marked "
              "complete here."]
    return "\n".join(lines)


def gh_publisher(repository: str, branch: str, title: str, body: str) -> str:
    out = subprocess.run(["gh", "pr", "create", "--repo", repository, "--base", "main", "--head", branch,
                          "--title", title, "--body", body], capture_output=True, text=True, check=False, timeout=120)
    if out.returncode != 0:
        raise RuntimeError("PROGRESS_PR_FAILED: " + out.stderr[-300:])
    return out.stdout.strip().splitlines()[-1]


def _git(clone: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True, check=False, timeout=timeout)


def record_progress(lane_root: Path, state_root: Path, repository: str, clone: Path, head: str, *,
                    items: list[dict[str, Any]], state: dict[str, Any], publications: dict[str, Any],
                    workspace: Path, publisher: Callable[[str, str, str, str], str] = gh_publisher,
                    remote_has_branch: Callable[[Path, str], bool] | None = None) -> dict[str, Any]:
    """Open at most one progress pull request for ``repository``; returns what happened (never raises on refusal)."""
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
    branch = branch_name(repository, completions)
    exists = remote_has_branch or (lambda repo_clone, name: bool(_git(repo_clone, "ls-remote", "--heads", "origin", name).stdout.strip()))
    if exists(clone, branch):
        return {"status": "ALREADY_OPEN", "branch": branch}
    shown = _git(clone, "show", f"{head}:{source}")
    if shown.returncode != 0:
        return {"status": "BLOCKED", "reason": "GRAPH_FILE_UNAVAILABLE"}
    try:
        text = apply_completions(shown.stdout, completions)
    except ProgressRefused as exc:
        return {"status": "BLOCKED", "reason": str(exc)}
    tree = Path(workspace) / branch.replace("/", "-")
    added = _git(clone, "worktree", "add", "-b", branch, str(tree), head)
    if added.returncode != 0:
        return {"status": "BLOCKED", "reason": "WORKTREE_PROVISION_FAILED", "detail": added.stderr[-200:]}
    try:
        (tree / source).write_text(text, encoding="utf-8", newline="")
        changed = _git(tree, "diff", "--name-only").stdout.split()
        if changed != [source]:
            return {"status": "BLOCKED", "reason": "UNEXPECTED_FILES_CHANGED", "files": changed}
        _git(tree, "add", source)
        committed = _git(tree, "-c", "user.name=Iron God", "-c", "user.email=iron-god@users.noreply.github.com",
                         "commit", "-q", "-m", f"{TITLE_PREFIX}: record {len(completions)} merged slice(s)")
        if committed.returncode != 0:
            return {"status": "BLOCKED", "reason": "COMMIT_FAILED", "detail": committed.stderr[-200:]}
        pushed = _git(tree, "push", "origin", f"{branch}:refs/heads/{branch}")
        if pushed.returncode != 0:
            return {"status": "BLOCKED", "reason": "PUSH_FAILED", "detail": pushed.stderr[-200:]}
        url = publisher(repository, branch, f"{TITLE_PREFIX}: record {len(completions)} merged slice(s)",
                        render_body(completions, source, head))
        return {"status": "OPENED", "branch": branch, "pr": url, "slices": [c["slice"] for c in completions]}
    finally:
        _git(clone, "worktree", "remove", str(tree))  # our own scratch worktree; a failure just leaves it behind
