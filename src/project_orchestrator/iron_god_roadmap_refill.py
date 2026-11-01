"""Roadmap-directed refill of the strict product backlog (issue #594).

The owner's roadmap is standing development authorization, but the lane dispatches only
well-formed ``approved`` backlog items. When a repository has none, this module connects
the two without a second planning system: it reuses the capability compiler (IG-060..067),
``compile_capability_authority`` for the repository's declared machine-readable graph,
the strict backlog contract and the development, working and shared memories.

For each repository without a dispatchable item it

1. compiles the graph the repository itself declares at its exact ``origin/main`` head
   (prose is never converted into mutation authority);
2. takes the dependency-ready, clear, non-duplicate capabilities and builds a ``SliceSpec``
   per bounded slice, writing an authoring *request* (objective, acceptance, allowed paths,
   baseline SHA, prior failures and development rules) for a qualified planner;
3. mechanically admits a planner's *draft* only if it satisfies the unchanged backlog
   contract plus roadmap traceability, scope, baseline, level, reservation and duplicate
   checks. An admitted item enters a runtime store beside the backlog state; it is never
   written into the hash-bound release and never marked done: only a merged outcome is.

Anything the compiler cannot decide becomes ``DIRECTION_REQUIRED`` with a focused question
and evidence, and a draft beyond the local builder's measured levels is recorded as a
capability blocker rather than reinterpreted. Every generated, admitted, rejected and
blocked candidate is appended to a bounded ledger and mirrored to working memory.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from scripts.capability_slice_contract import derive_slices

from . import iron_god_backlog as backlog
from . import iron_god_capable_builder as capable_builder
from . import iron_god_development_memory as development_memory
from .capability_authority import CapabilityAuthorityError, compile_capability_authority
from .grant_lifecycle import acquire_lifecycle_lock, release_lifecycle_lock
from .iron_god_calibration import capability_gate, load_profile
from .iron_god_ambiguity_detector import CLEAR, detect_ambiguity
from .iron_god_capability_graph_parser import CapabilityGraphInvalidError, parse_capability_graph
from .iron_god_dependency_resolution import DependencyResolutionError, resolve_dependencies
from .iron_god_duplicate_suppression import SUPPRESSED, suppress_duplicate_work
from .iron_god_slice_decomposition import DECOMPOSED, DIRECTION_REQUIRED, decompose_capability
from .iron_god_slicespec_generator import SliceSpecInputs, generate_slice_spec
from .state_io import atomic_json

ORIGIN = "roadmap-derived"
STORE = "roadmap-refill.json"
REQUESTS = "roadmap-requests"
DRAFTS = "roadmap-drafts"
LEDGER_LIMIT = 500
CHECK_INTERVAL_SECONDS = 20 * 60  # authority is re-read at most this often per repository
BASE_RE = re.compile(r"[0-9a-f]{40}")
LOCAL, CAPABLE, NONE = "LOCAL", "CAPABLE", "NONE"  # who may build a request: the local 7B, a stronger builder, nobody yet


def _now() -> str:
    return datetime.now(UTC).isoformat()


def store_path(lane_root: Path) -> Path:
    return Path(lane_root) / STORE


def load_store(lane_root: Path) -> dict[str, Any]:
    try:
        raw = json.loads(store_path(lane_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    def part(name: str, kind: type) -> Any:
        value = raw.get(name)
        return value if isinstance(value, kind) else kind()

    return {"schema_version": 1, "items": [i for i in part("items", list) if isinstance(i, dict)],
            "ledger": part("ledger", list), "checked": part("checked", dict), "processed": part("processed", dict)}


def _mutate(lane_root: Path, change: Callable[[dict[str, Any]], Any]) -> Any:
    """Serialize every read/modify/write of the store across processes; a crash leaves the last whole file."""
    Path(lane_root).mkdir(parents=True, exist_ok=True)  # the first write on a fresh state root must not crash
    lock = acquire_lifecycle_lock(store_path(lane_root))
    try:
        store = load_store(lane_root)
        result = change(store)
        store["ledger"] = store["ledger"][-LEDGER_LIMIT:]
        atomic_json(store_path(lane_root), store)
        return result
    finally:
        release_lifecycle_lock(lock)


def _log(store: dict[str, Any], kind: str, repository: str, ident: str, detail: Any) -> None:
    store["ledger"].append({"at": _now(), "kind": kind, "repository": repository, "id": ident, "detail": detail})


def admitted_items(lane_root: Path, existing: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Runtime-admitted items that still satisfy the strict contract and do not collide with the release backlog."""
    taken = {str(item.get("id")) for item in existing or []}
    kept: list[dict[str, Any]] = []
    for item in load_store(lane_root)["items"]:
        if str(item.get("id")) in taken or backlog.item_problems(item) or item.get("origin") != ORIGIN:
            continue  # a tampered or colliding entry is never dispatched
        declared = (item.get("authorization") or {}).get("declared_capability_level")
        if type(declared) is not int or item["level"] < declared:
            continue  # the item must prove the slice's declared level and never understate it
        taken.add(str(item["id"]))
        kept.append(item)
    return kept


def id_prefix(repository: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", f"RM-{repository.split('/', 1)[-1]}-")


def item_id(repository: str, capability_id: str, slice_id: str) -> str:
    """Stable identity of a capability's slice; slice ids are only unique within their capability, and the
    digest keeps sanitization or truncation from merging two different slices into one completion state."""
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{id_prefix(repository)}{capability_id}-{slice_id}")[:64]
    digest = hashlib.sha256(json.dumps([repository, capability_id, slice_id]).encode()).hexdigest()[:8]
    return f"{readable}-{digest}"


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out, index = "", 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out, index = out + "(?:.*/)?", index + 3
        elif pattern.startswith("**", index):
            out, index = out + ".*", index + 2
        elif pattern[index] == "*":
            out, index = out + "[^/]*", index + 1
        elif pattern[index] == "?":
            out, index = out + "[^/]", index + 1
        else:
            out, index = out + re.escape(pattern[index]), index + 1
    return re.compile(out + r"\Z")


def _matches(path: str, patterns: list[str]) -> bool:
    return any(_glob_regex(pattern.replace("\\", "/")).match(path) for pattern in patterns)


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def revisions(base_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if item.get("id") == base_id or str(item.get("id", "")).startswith(base_id + "-r")]


def _slice_status(base_id: str, items: list[dict[str, Any]], state: dict[str, Any],
                  delivered: frozenset[str] = frozenset()) -> tuple[str, str]:
    """(NEW|PENDING|DONE|FAILED, id to author next), from the lane's own record and never from the graph.

    ``delivered`` are request ids whose capable-builder pull request was merged; that is completion too.
    """
    chain = revisions(base_id, items)
    if not chain:
        if any(ident == base_id or ident.startswith(base_id + "-r") for ident in delivered):
            return "DONE", base_id
        return "NEW", base_id
    latest = chain[-1]
    row = state.get("items", {}).get(latest["id"], {})
    if backlog.is_done(latest, state):
        return "DONE", latest["id"]
    limit = backlog.DIRECT_ATTEMPTS if latest.get("direct") else backlog.MAX_ITEM_ATTEMPTS
    failed = str(row.get("outcome", "")).startswith("FAIL") and row.get("attempts", 0) >= limit
    if failed or latest.get("status") == "retired":
        return "FAILED", f"{base_id}-r{len(chain)}"
    return "PENDING", latest["id"]


def derive(repository: str, head: str, payload: dict[str, Any], source: str, *, items: list[dict[str, Any]],
           state: dict[str, Any], repo_root: Path | None = None, milestones: dict[str, str] | None = None,
           external_pending: frozenset[str] = frozenset(), external_done: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Run the existing compiler over the graph; returns requests plus what was skipped or needs direction.

    Slice selection is the supervisor's own ``derive_slices`` contract (roadmap order, slice dependencies,
    objective and acceptance required, product-completion proof), so both dispatch paths see identical slices.
    """
    result: dict[str, Any] = {"requests": [], "direction_required": [], "skipped": []}
    try:
        graph = parse_capability_graph(payload)
    except CapabilityGraphInvalidError as exc:
        result["direction_required"].append({
            "reason": "CAPABILITY_GRAPH_INVALID", "evidence": str(exc)[:600],
            "question": f"{source} fails validation; correct the declared capability graph so the engine can plan from it."})
        return result
    derived = derive_slices(payload, repository=repository, exact_head=head)
    if derived["status"] == "NO_WORK":
        result["skipped"].append({"reason": derived.get("reason") or "NO_WORK"})
        return result
    if derived["status"] != "READY":
        codes = [str(issue.get("code")) for issue in derived.get("issues", []) if isinstance(issue, dict)]
        result["direction_required"].append({
            "reason": codes[0] if codes else str(derived["status"]), "evidence": derived.get("issues", []),
            "question": f"{source} has no executable next slice ({', '.join(codes) or derived['status']}); "
                        "what bounded slice should advance the approved roadmap outcome?"})
        return result
    capability_id = derived["slices"][0]["capability_id"]
    node = next(n for n in graph.nodes if n.capability_id == capability_id)
    decomposition = decompose_capability(node)
    try:
        resolutions = (resolve_dependencies(repo_root, capability_id, node.depends_on,
                                            milestone_id_by_capability=milestones) if repo_root and milestones else ())
    except DependencyResolutionError as exc:
        result["direction_required"].append({
            "capability": capability_id, "reason": "DEPENDENCY_UNMAPPED", "evidence": str(exc)[:400],
            "question": f"Which milestone proves the dependencies of {capability_id}?"})
        return result
    ambiguity = detect_ambiguity(decomposition, resolutions)
    if decomposition.status != DECOMPOSED or ambiguity.status != CLEAR:
        result["direction_required" if decomposition.status in {DIRECTION_REQUIRED, DECOMPOSED} else "skipped"].append(
            {"capability": capability_id, "reason": ambiguity.reason if decomposition.status == DECOMPOSED
             else decomposition.reason, "evidence": list(ambiguity.unresolved_dependencies),
             "question": f"Capability {capability_id} is blocked or ambiguous in {source}; what is the intended next step?"}
            if decomposition.status in {DIRECTION_REQUIRED, DECOMPOSED} else {"capability": capability_id,
                                                                              "reason": decomposition.status})
        return result
    declared = {(cap.get("id"), row.get("id")): row for cap in payload.get("capabilities", []) if isinstance(cap, dict)
                for row in cap.get("slices", []) if isinstance(row, dict)}
    candidates = []
    for chosen in derived["slices"]:
        base_id = item_id(repository, capability_id, str(chosen["slice_id"]))
        status, expected = _slice_status(base_id, items, state, external_done)
        if status in {"PENDING", "DONE"}:
            result["skipped"].append({"capability": capability_id, "slice": chosen["slice_id"], "reason": status, "id": expected})
            continue
        if expected in external_pending:  # a stronger builder is already working on exactly this request
            result["skipped"].append({"capability": capability_id, "slice": chosen["slice_id"],
                                      "reason": "PENDING_CAPABLE_BUILDER", "id": expected})
            continue
        if not chosen["allowed_paths"]:
            result["direction_required"].append({
                "capability": capability_id, "slice": chosen["slice_id"], "reason": "ALLOWED_PATHS_MISSING", "evidence": [],
                "question": f"Slice {chosen['slice_id']} names no allowed paths; which files may it change?"})
            continue
        spec = generate_slice_spec(expected, decomposition, ambiguity, SliceSpecInputs(
            repository=repository, exact_baseline_sha=head, allowed_paths=tuple(chosen["allowed_paths"]),
            forbidden_paths=tuple(chosen["forbidden_paths"]), runner_requirements=tuple(chosen["runner_requirements"]),
            runtime_requirements=tuple(chosen["runtime_requirements"]), resource_requirements=(),
            product_reality_requirements=(), expected_artifacts=(), risk_class="standard",
            verification_policy="canonical-preflight-and-ci", estimated_conflict_scope=tuple(chosen["allowed_paths"])))
        candidates.append((spec, chosen, status, base_id))
        break  # one slice per capability at a time; the rest wait for it to merge
    verdicts = suppress_duplicate_work(tuple(spec for spec, *_rest in candidates))
    for (spec, chosen, status, base_id), verdict in zip(candidates, verdicts, strict=True):
        if verdict.status == SUPPRESSED:
            result["skipped"].append({"capability": spec.capability_id, "reason": "DUPLICATE", "id": spec.slice_id})
            continue
        failures = [{"id": item["id"], "outcome": state.get("items", {}).get(item["id"], {}).get("outcome")}
                    for item in revisions(base_id, items)] if status == "FAILED" else []
        result["requests"].append({
            "schema_version": 1, "id": spec.slice_id, "repository": repository, "exact_head": head,
            "roadmap_ref": f"{source}#{spec.capability_id}/{chosen['slice_id']}", "capability": spec.capability_id,
            "slice": chosen["slice_id"], "objective": chosen["objective"], "slice_acceptance": list(chosen["acceptance"]),
            "completion_evidence": list(spec.acceptance_criteria), "allowed_paths": list(spec.allowed_paths),
            "forbidden_paths": list(spec.forbidden_paths), "runtime_requirements": list(spec.runtime_requirements),
            "runner_requirements": list(spec.runner_requirements), "prior_failures": failures,
            "capability_level": declared.get((capability_id, chosen["slice_id"]), {}).get("capability_level"),
            "max_builder_level": max(backlog.BUILDABLE_LEVELS),
            "needs": "a backlog item (level 1 build spec, or level 2 directed edit with approved tests that fail at "
                     "exact_head) whose id and roadmap_ref equal this request's, written to the drafts directory"})
    return result


def route(request: dict[str, Any], *, profile: dict[str, Any] | None, max_level: int,
          unsupported: str | None = None) -> dict[str, Any]:
    """Which builder may take a request, from declared level + the measured profile + the lane's buildable levels.

    The local worker is only assigned work inside what was measured: the slice must declare ``capability_level``,
    the calibration profile must match this model and hardware and pass that level, and the product lane must
    build that level. Anything else goes to a stronger builder or nobody, never silently simplified.
    """
    level = request.get("capability_level")
    if unsupported:
        return {"builder": NONE, "reason": unsupported, "level": level}
    gate = capability_gate(profile, {"capability_level": level})
    if gate is not None:
        # An undeclared or uncalibrated level is not evidence the local builder can do it.
        return {"builder": CAPABLE, "reason": gate, "level": level}
    limit = min(max_level, max(backlog.BUILDABLE_LEVELS))
    if level not in backlog.BUILDABLE_LEVELS or level > limit:
        return {"builder": CAPABLE, "reason": "LEVEL_BEYOND_PRODUCT_LANE", "level": level, "lane_limit": limit}
    return {"builder": LOCAL, "reason": "WITHIN_MEASURED_LOCAL_CAPABILITY", "level": level, "lane_limit": limit}


def draft_problems(draft: Any, request: dict[str, Any], *, items: list[dict[str, Any]], reserved: set[str],
                   head: str, changed_since: Callable[[str, str], set[str]], max_level: int) -> list[str]:
    """Why a planner's draft may not enter the backlog (empty when it may)."""
    if not isinstance(draft, dict):
        return ["DRAFT_NOT_AN_OBJECT"]
    candidate = {**draft, "status": "approved", "assignee": backlog.AGENT_ID}
    problems = list(backlog.item_problems(candidate))
    if draft.get("id") != request["id"]:
        problems.append("ID_MISMATCH")
    if draft.get("repository") != request["repository"]:
        problems.append("REPOSITORY_MISMATCH")
    if draft.get("roadmap_ref") != request["roadmap_ref"]:
        problems.append("ROADMAP_REF_MISMATCH")
    addresses = str(draft.get("addresses", ""))
    if request["capability"] not in addresses and str(request["slice"]) not in addresses:
        problems.append("NOT_TRACEABLE_TO_ROADMAP_OUTCOME")
    if type(draft.get("level")) is int and (draft["level"] not in backlog.BUILDABLE_LEVELS or draft["level"] > max_level):
        problems.append("LEVEL_BEYOND_LOCAL_BUILDER")
    # Admission feeds the LOCAL builder's queue, so the request itself must have been routed there, and the draft
    # may not understate the slice's declared level (that would be silently simplifying the work).
    route = request.get("route")
    if not isinstance(route, dict) or route.get("builder") != LOCAL:
        problems.append("REQUEST_NOT_ROUTED_TO_LOCAL_BUILDER")
    declared_level = request.get("capability_level")
    if type(draft.get("level")) is int and (type(declared_level) is not int or draft["level"] < declared_level):
        problems.append("DRAFT_UNDERSTATES_DECLARED_LEVEL")
    files = backlog.edit_files(candidate)
    if not files:
        problems.append("NO_EDIT_FILES")
    if any(not _matches(path, request["allowed_paths"]) or _matches(path, request["forbidden_paths"]) for path in files):
        problems.append("PATH_OUTSIDE_ROADMAP_SCOPE")
    if files & reserved:
        problems.append("FILE_RESERVED_BY_OPEN_WORK")
    if candidate.get("direct"):
        base = str(draft.get("approved_base") or "")
        if not BASE_RE.fullmatch(base):
            problems.append("APPROVED_BASE_MISSING")
        elif base != head:
            try:
                if changed_since(base, head) & files:
                    problems.append("APPROVED_BASE_STALE")
            except Exception:  # an unreadable history is not evidence that the base is current
                problems.append("APPROVED_BASE_UNVERIFIABLE")
    superseded = {str(row.get("id")) for row in request.get("prior_failures", []) if isinstance(row, dict)}
    objective = " ".join(str(draft.get("objective", "")).split()).lower()
    target = (candidate["direct"].get("module_path"), sorted(candidate["direct"].get("targets") or [])) \
        if isinstance(candidate.get("direct"), dict) else None
    for other in items:
        if other.get("repository") != request["repository"] or other.get("status") == "retired":
            continue
        if other.get("id") in superseded:
            continue  # a failed predecessor of this very revision: re-authoring it is the point, not a duplicate
        other_direct = other.get("direct") if isinstance(other.get("direct"), dict) else None
        if " ".join(str(other.get("objective", "")).split()).lower() == objective:
            problems.append("DUPLICATE_OBJECTIVE")
        elif target and other_direct and (other_direct.get("module_path"), sorted(other_direct.get("targets") or [])) == target:
            problems.append("DUPLICATE_TARGET")
    return sorted(set(problems))


def stamp(draft: dict[str, Any], request: dict[str, Any], source: str) -> dict[str, Any]:
    item = {**draft, "status": "approved", "assignee": backlog.AGENT_ID, "origin": ORIGIN}
    item["authorization"] = {
        "basis": "standing owner-approved roadmap (development-scope delegation 2026-11-01, issue #594)",
        "authority": source, "exact_head": request["exact_head"], "capability": request["capability"],
        "slice": request["slice"], "declared_capability_level": request.get("capability_level"),
        "route": (request.get("route") or {}).get("builder"),
        "grants": "development only; no execution, release or control authority"}
    return item


def _git_changed(clone: Path) -> Callable[[str, str], set[str]]:
    def changed(base: str, head: str) -> set[str]:
        out = subprocess.run(["git", "diff", "--name-only", f"{base}..{head}"], cwd=clone, capture_output=True,
                             text=True, check=True, timeout=120)
        return {line.strip().replace("\\", "/") for line in out.stdout.splitlines() if line.strip()}
    return changed


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def process_drafts(lane_root: Path, repository: str, requests: list[dict[str, Any]], *, items: list[dict[str, Any]],
                   reserved: set[str], head: str, changed_since: Callable[[str, str], set[str]], max_level: int,
                   source: str) -> dict[str, list[str]]:
    """Admit or reject each draft file once per distinct content; unchanged drafts are not re-judged."""
    outcome: dict[str, list[str]] = {"admitted": [], "rejected": [], "blocked": []}
    for request in requests:
        path = Path(lane_root) / DRAFTS / f"{request['id']}.json"
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        draft = _read_json(path)

        def judge(store: dict[str, Any], request: dict[str, Any] = request, digest: str = digest, draft: Any = draft) -> None:
            if store["processed"].get(request["id"]) == digest:
                return
            ids = {item.get("id") for item in items}
            known = [*items, *[i for i in store["items"] if i.get("id") not in ids]]
            problems = draft_problems(draft, request, items=known, reserved=reserved, head=head,
                                      changed_since=changed_since, max_level=max_level)
            store["processed"][request["id"]] = digest
            if not problems:
                store["items"].append(stamp(draft, request, source))
                _log(store, "ADMITTED", repository, request["id"], {"roadmap_ref": request["roadmap_ref"]})
                outcome["admitted"].append(request["id"])
            elif "LEVEL_BEYOND_LOCAL_BUILDER" in problems:
                _log(store, "CAPABILITY_BLOCKER", repository, request["id"],
                     {"problems": problems, "route": "a capable, separately authorized builder"})
                outcome["blocked"].append(request["id"])
            else:
                _log(store, "REJECTED", repository, request["id"], {"problems": problems})
                outcome["rejected"].append(request["id"])

        _mutate(lane_root, judge)
    return outcome


def refill_repository(lane_root: Path, state_root: Path, repository: str, clone: Path, head: str, *,
                      items: list[dict[str, Any]], state: dict[str, Any], reserved: set[str], max_level: int,
                      changed_since: Callable[[str, str], set[str]] | None = None,
                      unsupported: Callable[[Path, str], str | None] | None = None) -> dict[str, Any]:
    """One planning pass for a repository without a dispatchable item; bad authority becomes DIRECTION_REQUIRED."""
    changed_since = changed_since or _git_changed(clone)

    def show(_repo: str, _head: str, path: str) -> str:
        proc = subprocess.run(["git", "show", f"{head}:{path}"], cwd=clone, capture_output=True, text=True,
                              check=False, timeout=60)
        if proc.returncode:
            raise CapabilityAuthorityError(f"AUTHORITY_FILE_UNAVAILABLE:{path}")
        return proc.stdout

    report: dict[str, Any] = {"repository": repository, "exact_head": head, "requests": [], "admitted": [],
                              "rejected": [], "blocked": [], "direction_required": [], "skipped": [],
                              "routed": {LOCAL: [], CAPABLE: [], NONE: []}}
    source = ""
    try:
        control_plane = json.loads(show(repository, head, "engineering/control-plane.json"))
        compiled = compile_capability_authority(state_root, repository, head, control_plane, read_authoritative_file=show)
        source = compiled["graph"]["authority"]["source"]
        derived = derive(repository, head, compiled["graph"], source, items=items, state=state,
                         external_pending=frozenset(capable_builder.open_request_ids(lane_root)),
                         external_done=frozenset(capable_builder.merged_request_ids(lane_root)))
    except (CapabilityAuthorityError, ValueError, OSError, subprocess.SubprocessError) as exc:
        reason = str(exc)[:300] or type(exc).__name__
        derived = {"requests": [], "skipped": [], "direction_required": [{
            "reason": reason.split(":")[0] or "ROADMAP_AUTHORITY_UNAVAILABLE", "evidence": reason,
            "question": "Declare this repository's machine-readable capability graph (engineering/control-plane.json "
                        "capability_graph, listed in authoritative_documents) from its owner-approved roadmap, or say "
                        "which document is the authority."}]}
    try:
        rules = development_memory.rules_for(lane_root, repository)
    except Exception:  # memory is advisory evidence for the planner, never a gate
        rules = []
    profile = load_profile(state_root)
    try:
        repo_problem = unsupported(clone, head) if unsupported else None
    except Exception as exc:  # an unreadable tree is not evidence the repository can be built
        repo_problem = f"REPOSITORY_UNREADABLE: {type(exc).__name__}"
    for request in derived["requests"]:
        request["development_rules"] = rules
        request["route"] = route(request, profile=profile, max_level=max_level, unsupported=repo_problem)
        report["routed"][request["route"]["builder"]].append(request["id"])
    for request in derived["requests"]:
        # Open pull requests own files; a capable slice may touch anything its allowed paths cover.
        request["reserved_conflicts"] = sorted(path for path in reserved if _matches(path, request["allowed_paths"]))
    report["capable_requests"] = [request for request in derived["requests"] if request["route"]["builder"] == CAPABLE]
    report["direction_required"], report["skipped"] = derived["direction_required"], derived["skipped"]
    report["requests"] = [request["id"] for request in derived["requests"]]
    requests_dir = Path(lane_root) / REQUESTS
    requests_dir.mkdir(parents=True, exist_ok=True)
    for request in derived["requests"]:
        atomic_json(requests_dir / f"{request['id']}.json", request)
    if derived["requests"]:
        report.update(process_drafts(lane_root, repository, derived["requests"], items=items, reserved=reserved,
                                     head=head, changed_since=changed_since, max_level=max_level, source=source))

    def record(store: dict[str, Any]) -> None:
        store["checked"][repository] = {"at": _now(), "head": head}
        recent = store["ledger"][-100:]
        for ident in report["requests"]:
            if not any(e["kind"] == "GENERATED" and e["id"] == ident and e["detail"].get("head") == head for e in recent):
                _log(store, "GENERATED", repository, ident, {"head": head})
        for builder in (CAPABLE, NONE):
            for ident in report["routed"][builder]:
                reason = next(r["route"]["reason"] for r in derived["requests"] if r["id"] == ident)
                if not any(e["kind"] == "CAPABILITY_BLOCKER" and e["id"] == ident and e["detail"].get("head") == head
                           and e["detail"].get("reason") == reason for e in recent):
                    _log(store, "CAPABILITY_BLOCKER", repository, ident,
                         {"head": head, "builder_needed": builder, "reason": reason,
                          "route": "a capable, separately authorized builder" if builder == CAPABLE
                          else "none available for this repository yet"})
        for row in report["direction_required"]:
            key = json.dumps(row, sort_keys=True)
            if not any(e["kind"] == "DIRECTION_REQUIRED" and e["repository"] == repository
                       and json.dumps(e["detail"], sort_keys=True) == key for e in recent):
                _log(store, "DIRECTION_REQUIRED", repository, str(row.get("capability") or repository), row)

    _mutate(lane_root, record)
    return report


def log_event(lane_root: Path, kind: str, repository: str, ident: str, detail: Any) -> None:
    """Append one outcome (for example a capable-builder dispatch) to the same bounded ledger."""
    _mutate(lane_root, lambda store: _log(store, kind, repository, ident, detail))


def due(lane_root: Path, repository: str, *, now: datetime | None = None) -> bool:
    store = load_store(lane_root)
    prefix = id_prefix(repository)
    directory = Path(lane_root) / DRAFTS
    for draft in directory.glob("*.json") if directory.is_dir() else []:
        if draft.stem.startswith(prefix) and store["processed"].get(draft.stem) != hashlib.sha256(draft.read_bytes()).hexdigest():
            return True  # an unjudged draft is admitted promptly rather than after the interval
    row = store["checked"].get(repository)
    if not isinstance(row, dict):
        return True
    try:
        elapsed = ((now or datetime.now(UTC)) - datetime.fromisoformat(str(row["at"]))).total_seconds()
    except (KeyError, ValueError):
        return True
    return elapsed >= CHECK_INTERVAL_SECONDS


def summarize(report: dict[str, Any]) -> str:
    return (f"requests {len(report['requests'])}; admitted {len(report['admitted'])}; rejected {len(report['rejected'])}; "
            f"capability blockers {len(report['blocked'])}; direction required {len(report['direction_required'])}; "
            f"skipped {len(report['skipped'])}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Show roadmap-refill requests, admitted items and the ledger.")
    parser.add_argument("lane_root", type=Path)
    args = parser.parse_args(argv)
    store = load_store(args.lane_root)
    directory = Path(args.lane_root) / REQUESTS
    requests = sorted(directory.glob("*.json")) if directory.is_dir() else []
    print(json.dumps({"requests": [p.stem for p in requests], "admitted": [i["id"] for i in store["items"]],
                      "ledger": store["ledger"][-20:]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
